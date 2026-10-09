"""媒体生成相关的步骤与辅助：

- `media` 步骤（讨论模式，提问时选了输出类型）：把汇总出的生成提示词交给媒体模型生成 →
  带 vision 标签的成员评审结果（图片、视频截帧真正发给他们）→ 统筹决定是否改提示词重新生成，
  最多 media.max_rounds 轮。语音只生成一次（没有可评审的画面）。
- `media_gate()`：视频每次生成前都必须用户确认；图片、语音超过单题门槛才确认；预算不足时弹预算卡片。
- 协同模式的媒体子任务（steps/collab.py）也通过这里生成。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Any

from pydantic import Field

from roundtable.core.allocation import shuffled
from roundtable.core.cards import CardOption, ConfirmationCard, money
from roundtable.core.jsonout import extract_json_object
from roundtable.core.media import KIND_LABELS, MediaResult, Placement
from roundtable.core.prompts import RenderedPrompt
from roundtable.core.providers import Media

from .base import (
    NeedsApproval,
    StepFailed,
    StepResult,
    TableContext,
    call_and_parse,
    neutralize,
    register_step,
)
from .schemas import _Loose

STEP = "media"
TAGS = ("generation_prompt", "request", "result", "review")


# --- 确认 ---------------------------------------------------------------------


def media_gate(
    ctx: TableContext, key: str, kinds: list[str], *, what: str, chars: int = 0, reason: str = ""
) -> str:
    """返回 "go"（生成）或 "skip"（用户选择不生成）；需要用户确认且还没回复时抛 NeedsApproval。"""
    assert ctx.media is not None
    rules = ctx.config.roundtable.media
    cost = sum(ctx.media.estimate(k, ctx.media_tier, chars=chars) for k in kinds)
    budget_card = ctx.budget_gate(cost)
    if budget_card is not None:
        raise NeedsApproval(budget_card)
    threshold = ctx.config.routing.confirm_threshold_usd
    if not ((rules.confirm_video and "video" in kinds) or cost > threshold):
        return "go"
    answer = ctx.approval(key)
    if answer == "generate":
        return "go"
    if answer == "skip":
        return "skip"
    names = "、".join(f"{KIND_LABELS[k]}" for k in kinds)
    situation = [f"{what}：将生成 {len(kinds)} 个媒体成果（{names}），预计 {money(cost)}。"]
    if "video" in kinds:
        situation.append("视频生成较贵且需要等待，每次生成前都会先问你。")
    if cost > threshold:
        situation.append(f"超过单题确认门槛 {money(threshold)}。")
    card = ConfirmationCard(
        kind="media",
        situation="\n".join(situation),
        options=(
            CardOption("generate", "生成", cost, "按预估计费，实际以渠道返回为准"),
            CardOption("skip", "不生成", 0.0, "跳过这一次生成，其余部分照常进行"),
            CardOption("stop", "停止", 0.0, "保留已完成的部分"),
        ),
        recommendation="generate",
        reason=reason or "生成后会由评审者检查，必要时还会询问是否重新生成",
    )
    raise NeedsApproval(card, key)


# --- 评审与决定的数据结构 ---------------------------------------------------------


class _Problem(_Loose):
    what: str = ""
    fix: str = ""


class _Check(_Loose):
    item: str = ""
    ok: bool = False
    note: str = ""


class MediaReviewOutput(_Loose):
    satisfied: bool = False
    problems: list[_Problem] = Field(default_factory=list)
    checks: list[_Check] = Field(default_factory=list)  # media_review/v2：风格清单逐条判定
    checked: str = ""


class MediaDecisionOutput(_Loose):
    satisfied: bool = False
    prompt: str = ""
    reason: str = ""


@dataclass(frozen=True)
class MediaReview:
    satisfied: bool
    problems: tuple[tuple[str, str], ...]
    checked: str
    checks: tuple[tuple[str, bool, str], ...] = ()  # (清单项, 是否符合, 依据)

    @property
    def valid(self) -> bool:
        """说"可以交付"必须写明检查了什么；说"不行"必须指出具体问题。"""
        if self.satisfied:
            return len(self.checked.strip()) >= 8
        return any(what.strip() for what, _ in self.problems)

    def to_dict(self) -> dict[str, Any]:
        return {
            "satisfied": self.satisfied,
            "problems": [{"what": w, "fix": f} for w, f in self.problems],
            "checks": [{"item": i, "ok": ok, "note": n} for i, ok, n in self.checks],
            "checked": self.checked,
            "valid": self.valid,
        }


@dataclass(frozen=True)
class MediaDecision:
    satisfied: bool
    prompt: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"satisfied": self.satisfied, "prompt": self.prompt, "reason": self.reason}


def parse_media_review(text: str) -> MediaReview:
    out = MediaReviewOutput.model_validate(extract_json_object(text))
    problems = tuple((p.what.strip(), p.fix.strip()) for p in out.problems)
    checks = tuple((c.item.strip(), c.ok, c.note.strip()) for c in out.checks if c.item.strip())
    # 清单里任意一条不符合，就不能说"可以交付"（代码保证，不信模型自己的 satisfied）
    satisfied = out.satisfied and all(ok for _, ok, _ in checks)
    if not satisfied and not any(w for w, _ in problems):
        failed = [f"不符合风格清单：{i}" for i, ok, _ in checks if not ok]
        problems = tuple((f, "按清单要求修改提示词") for f in failed) or problems
    return MediaReview(satisfied, problems, out.checked.strip(), checks)


def parse_media_decision(text: str) -> MediaDecision:
    out = MediaDecisionOutput.model_validate(extract_json_object(text))
    if not out.satisfied and not out.prompt.strip():
        raise ValueError("需要重新生成时必须给出修改后的提示词")
    return MediaDecision(out.satisfied, out.prompt.strip(), out.reason.strip())


# --- 辅助 ---------------------------------------------------------------------


def media_payload(res: MediaResult, kind: str, round_no: int) -> dict[str, Any]:
    """写进成员产出（work / rework）的媒体信息：文件、轮次、是否成功（不含模型身份）。"""
    return {
        "kind": kind,
        "round": round_no,
        "ok": res.ok,
        "file_id": res.file_id,
        "cost_usd": res.cost_usd,
        "error": res.error,
        "references": res.references,
        "warning": res.warning,
    }


def media_review_prompt(
    ctx: TableContext, code: str, kind: str, prompt: str, note: str, medias: list[Media]
) -> RenderedPrompt:
    """评审生成结果的提示词（结果图片 / 视频截帧作为随附图片发给评审者）。"""
    base = ctx.prompts.get("media_review", ctx.prompt_version("media_review")).render(
        code=ctx.label(code),
        medium=KIND_LABELS[kind],
        question=ctx.question.text,
        generation_prompt=neutralize(ctx.scrub(prompt), TAGS),
        result=note,
    )
    user = next(i for i, m in enumerate(base.messages) if m.role == "user")
    messages = list(base.messages)
    messages[user] = replace(messages[user], media=tuple(medias))
    return replace(base, messages=tuple(messages))


def result_note(ctx: TableContext, kind: str, res: MediaResult) -> tuple[str, list[Media]]:
    """评审者看到的生成结果：说明文字 + 随附图片（图片本身，或视频的截帧）。"""
    assert ctx.media is not None
    files = [ctx.repo.file(ctx.session_id, f) for f in res.file_ids]
    medias: list[Media] = []
    if kind == "image":
        for row in files:
            data = ctx.file_store.load(row["storage_key"]) if ctx.file_store else b""
            if data and row["mime"] in ("image/png", "image/jpeg", "image/webp", "image/gif"):
                medias.append(Media("image", row["mime"], data, row["path"]))
        body = f"生成了 {len(files)} 张图片，已作为随附图片发给你（随附图片 1–{len(medias)}）。"
    elif kind == "video":
        medias = ctx.video_frames(files[0]["id"]) if files else []
        body = (
            f"生成了 1 个视频，按时间均匀截取了 {len(medias)} 帧，依次作为随附图片发给你。"
            if medias
            else "生成了 1 个视频，但无法截帧，你只能依据提示词做判断，并在 checked 中说明。"
        )
    else:
        body = "生成了 1 段语音（你无法收听），请检查文稿本身是否适合朗读。"
    return f'<result type="{kind}">\n{neutralize(body, TAGS)}\n</result>', medias


# --- 讨论模式的 media 步骤 -----------------------------------------------------------


class MediaStep:
    name = STEP

    async def run(self, ctx: TableContext) -> StepResult:
        kind = ctx.media_kind
        if ctx.media is None or kind is None:
            return StepResult(self.name, notes=("本场没有选择媒体输出",))
        synthesis = ctx.state.synthesis
        if synthesis is None or not synthesis.output.final_answer.strip():
            raise StepFailed("没有可用的生成提示词（汇总结果为空）")
        rules = ctx.config.roundtable.media
        reason = ctx.media.unavailable_reason(kind, ctx.media_tier)
        if reason:
            return StepResult(self.name, notes=(reason,))
        notes: list[str] = []
        calls = 0
        prompt = synthesis.output.final_answer.strip()
        for round_no in range(1, rules.max_rounds + 1):
            if round_no > 1:
                prompt = self._next_prompt(ctx, round_no - 1) or prompt
            gate = media_gate(
                ctx,
                f"media:{ctx.table_no}:{round_no}",
                [kind],
                what=f"第 {round_no} 轮（最多 {rules.max_rounds} 轮）",
                chars=len(prompt),
                reason=self._decision_reason(ctx, round_no - 1),
            )
            if gate == "skip":
                notes.append(f"第 {round_no} 轮按你的选择没有生成")
                break
            res = await ctx.media.generate(
                kind,
                prompt,
                Placement(ctx.table_no, self.name, round_no),
                tier=ctx.media_tier,
                references=ctx.style_references() if kind == "image" else (),
            )
            if not res.ok:
                notes.append(f"第 {round_no} 轮生成失败：{res.error}")
                break
            if res.warning and res.warning not in notes:
                notes.append(res.warning)
            if round_no == rules.max_rounds or kind == "speech":
                break
            made, decision = await self._review_and_decide(ctx, kind, prompt, res, round_no)
            calls += made
            if decision is None:
                notes.append("无法评审生成结果，保留当前版本")
                break
            if decision.satisfied or decision.prompt.strip() == prompt.strip():
                break
        return StepResult(self.name, calls=calls, notes=tuple(notes))

    # --- 持久化的评审与决定 ---------------------------------------------------------

    def _rows(self, ctx: TableContext, kind: str, round_no: int) -> list[dict[str, Any]]:
        import json

        out = []
        for o in ctx.repo.outputs(ctx.session_id, table_no=ctx.table_no, kind=kind):
            data = json.loads(o["content"])
            if o["step"] == STEP and data.get("round") == round_no:
                out.append({"code": o["code"], **data})
        return out

    def _next_prompt(self, ctx: TableContext, round_no: int) -> str | None:
        rows = self._rows(ctx, "media_decision", round_no)
        return rows[-1]["prompt"] if rows and not rows[-1]["satisfied"] else None

    def _decision_reason(self, ctx: TableContext, round_no: int) -> str:
        rows = self._rows(ctx, "media_decision", round_no) if round_no else []
        return f"统筹的决定：{rows[-1]['reason']}" if rows and rows[-1]["reason"] else ""

    def _save(self, ctx: TableContext, kind: str, data: dict, code=None, call_id=None) -> None:
        ctx.repo.save_output(
            ctx.session_id,
            table_no=ctx.table_no,
            step=STEP,
            kind=kind,
            code=code,
            content=data,
            call_id=call_id,
        )

    async def _review_and_decide(
        self, ctx: TableContext, kind: str, prompt: str, res: MediaResult, round_no: int
    ) -> tuple[int, MediaDecision | None]:
        rules = ctx.config.roundtable.media
        existing = self._rows(ctx, "media_decision", round_no)
        if existing:
            d = existing[-1]
            return 0, MediaDecision(d["satisfied"], d["prompt"], d["reason"])
        calls = 0
        # 评审者：带 vision 标签的在场成员（媒体成果的画面必须真正发给他们）
        seeing = [c for c in ctx.active if "vision" in ctx.models_by_id[ctx.members[c]].tags]
        reviewers = shuffled(seeing, ctx.rng)[: rules.reviewers]
        if not reviewers:
            return 0, None
        done = {r["code"]: r for r in self._rows(ctx, "media_review", round_no)}
        note, medias = result_note(ctx, kind, res)

        async def review(code: str) -> None:
            nonlocal calls
            rendered = media_review_prompt(ctx, code, kind, prompt, note, medias)
            parsed = await call_and_parse(
                ctx,
                step=STEP,
                role="member",
                model_id=ctx.members[code],
                prompt=rendered,
                code=code,
                parse=parse_media_review,
            )
            calls += 1
            if parsed.failed_call:
                ctx.drop(code, STEP, "调用失败")
            value = parsed.value
            self._save(
                ctx,
                "media_review",
                {
                    "round": round_no,
                    **(value.to_dict() if value else {"valid": False, "satisfied": False}),
                    "degraded": value is None,
                },
                code=code,
                call_id=parsed.call_id,
            )

        await asyncio.gather(*(review(c) for c in reviewers if c not in done))
        reviews = [r for r in self._rows(ctx, "media_review", round_no) if r.get("valid")]
        if not reviews:
            return calls, None
        decision = await self._decide(ctx, kind, prompt, reviews, round_no)
        calls += 1
        if decision is not None:
            self._save(ctx, "media_decision", {"round": round_no, **decision.to_dict()})
        return calls, decision

    async def _decide(
        self,
        ctx: TableContext,
        kind: str,
        prompt: str,
        reviews: list[dict[str, Any]],
        round_no: int,
    ) -> MediaDecision | None:
        rules = ctx.config.roundtable.media
        blocks = []
        for n, r in enumerate(shuffled(reviews, ctx.rng), 1):
            lines = ["可以交付" if r["satisfied"] else "需要改进"]
            lines += [f"问题：{p['what']}；建议：{p['fix']}" for p in r.get("problems", [])]
            lines += [
                f"不符合风格清单：{c['item']}（{c.get('note', '')}）"
                for c in r.get("checks", [])
                if not c["ok"]
            ]
            if r.get("checked"):
                lines.append(f"检查了：{r['checked']}")
            blocks.append(f'<review no="{n}">\n{neutralize(chr(10).join(lines), TAGS)}\n</review>')
        rendered = ctx.prompts.get("media_refine", ctx.prompt_version("media_refine")).render(
            medium=KIND_LABELS[kind],
            question=ctx.question.text,
            generation_prompt=neutralize(ctx.scrub(prompt), TAGS),
            reviews="\n\n".join(blocks),
            round=str(round_no),
            max_rounds=str(rules.max_rounds),
        )
        assert ctx.coordinator
        parsed = await call_and_parse(
            ctx,
            step=STEP,
            role="coordinator",
            model_id=ctx.coordinator,
            prompt=rendered,
            parse=parse_media_decision,
        )
        return parsed.value


register_step(MediaStep())
