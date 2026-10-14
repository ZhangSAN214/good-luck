"""风格规范与风格校验（阶段 22）。

- `style` 步骤（讨论 / 协同两种模式共用，放在流程最前面）：题目带风格参考图又要画图时，
  带 vision 标签的成员（最多 style.reviewers 位）各自看原图写风格描述，统筹合并成一份**风格规范**
  和 6–10 条可逐条检查的**风格清单**，存为 outputs 的 style_spec；之后的每次调用都会带上它
  （TableContext.messages_for）。没有 vision 成员时改用图片的文字版并提示"可能不准"。
- `style_gate()`：生成的图片由非作者的 vision 成员对照风格清单逐条判定"符合 / 不符合"，
  任意一条不符合即不通过：作者按意见改提示词重画，最多 media.style_retries 次；
  仍不通过时保留最后一版，标记"风格未通过"。每次判定、重画都落库，恢复时不重复调用。
"""

from __future__ import annotations

import asyncio
import json
import random
from dataclasses import dataclass
from typing import Any

from pydantic import Field

from roundtable.core.allocation import shuffled
from roundtable.core.cards import ConfirmationCard
from roundtable.core.jsonout import extract_json_object
from roundtable.core.media import KIND_LABELS, MediaResult, Placement

from .base import (
    StepFailed,
    StepResult,
    TableContext,
    call_and_parse,
    call_model,
    neutralize,
    register_step,
)
from .media import TAGS as MEDIA_TAGS
from .media import MediaReview, media_review_prompt, parse_media_review, result_note
from .schemas import StyleSpec, _Loose, parse_revision

STEP = "style"
GATE = "style_gate"
EXTRACT_TAGS = ("extraction", "question")
NO_VISION_WARNING = "没有带 vision 标签的成员，风格规范来自图片的文字版，可能不准"


class StyleOutput(_Loose):
    spec: str = Field(min_length=1)
    checklist: list[str] = Field(default_factory=list)


def parse_style(text: str, max_items: int) -> tuple[str, tuple[str, ...]]:
    """合并结果 → (规范, 清单)。清单去空、去重、最多 max_items 条；规范或清单为空视为格式不符。"""
    out = StyleOutput.model_validate(extract_json_object(text))
    items = tuple(dict.fromkeys(i.strip() for i in out.checklist if i.strip()))[:max_items]
    if not out.spec.strip() or not items:
        raise ValueError("风格规范或风格清单为空")
    return out.spec.strip(), items


def style_references_named(ctx: TableContext) -> str:
    names = [a.name for a in ctx.attachments if a.kind == "image" and a.style_ref]
    return "、".join(names) or "（无）"


def wants_style(ctx_attachments, drawing: bool, enabled: bool) -> bool:
    """有标注为风格参考的图片附件、而且会用到画图时才提取风格规范。"""
    return enabled and drawing and any(a.kind == "image" and a.style_ref for a in ctx_attachments)


class StyleStep:
    name = STEP

    async def run(self, ctx: TableContext) -> StepResult:
        if ctx.state.style is not None:
            return StepResult(self.name)
        rules = ctx.config.roundtable.style
        refs = [a for a in ctx.attachments if a.kind == "image" and a.style_ref]
        if not refs:
            return StepResult(self.name, notes=("没有风格参考图",))
        if not ctx.coordinator:
            raise StepFailed("风格规范需要统筹合并")
        names = style_references_named(ctx)
        seeing = [c for c in ctx.active if "vision" in ctx.models_by_id[ctx.members[c]].tags]
        picked = shuffled(seeing, random.Random(f"{ctx.session_id}:{ctx.table_no}:style"))
        picked = picked[: rules.reviewers]
        texts: dict[str, str] = {}
        calls = 0

        async def extract(code: str) -> None:
            nonlocal calls
            prompt = ctx.prompts.get("style_extract", ctx.prompt_version("style_extract")).render(
                code=ctx.label(code), question=ctx.question.text, references=names
            )
            out = await call_model(
                ctx,
                step=self.name,
                role="member",
                model_id=ctx.members[code],
                prompt=prompt,
                code=code,
            )
            calls += 1
            text = out.completion.text.strip() if out.completion else ""
            if out.completion is None:
                ctx.drop(code, self.name, "调用失败")
            elif text:
                texts[code] = text

        await asyncio.gather(*(extract(c) for c in picked))
        text_only = not texts
        if text_only:  # 没有 vision 成员（或都失败了）：用图片的文字版
            blocks = [
                (a.name, a.text) for a in refs if a.text and a.text_source in ("vision", "extract")
            ] or [(a.name, a.text) for a in refs if a.text]
            if not blocks:
                return StepResult(
                    self.name, calls=calls, notes=("参考图没有可用的文字版，跳过风格规范",)
                )
            extractions = "\n\n".join(
                '<extraction code="文字版">\n'
                f"{neutralize(ctx.scrub(f'{n}：{t}'), EXTRACT_TAGS)}\n</extraction>"
                for n, t in blocks
            )
            source_note = (
                "注意：以下描述来自图片的文字版（没有成员能直接看图），可能不准，"
                "不确定的地方标注存疑。"
            )
        else:
            order = shuffled(sorted(texts), ctx.rng)
            extractions = "\n\n".join(
                f'<extraction code="{ctx.label(c)}">\n'
                f"{neutralize(ctx.scrub(texts[c]), EXTRACT_TAGS)}\n</extraction>"
                for c in order
            )
            source_note = ""
        prompt = ctx.prompts.get("style_merge", ctx.prompt_version("style_merge")).render(
            question=ctx.question.text,
            references=names,
            extractions=extractions,
            min_items=str(rules.checklist_min),
            max_items=str(rules.checklist_max),
            source_note=source_note,
        )
        parsed = await call_and_parse(
            ctx,
            step=self.name,
            role="coordinator",
            model_id=ctx.coordinator,
            prompt=prompt,
            parse=lambda t: parse_style(t, rules.checklist_max),
        )
        calls += 1
        warning = NO_VISION_WARNING if text_only else None
        if parsed.value is not None:
            spec, checklist = parsed.value
            style = StyleSpec(
                ctx.scrub(spec),
                tuple(ctx.scrub(i) for i in checklist),
                tuple(sorted(texts)),
                text_only,
                warning,
            )
        else:  # 合并失败：把各份描述拼起来当规范，没有清单（之后不做逐条校验）
            body = "\n\n".join(ctx.scrub(texts[c]) for c in sorted(texts)) or ctx.scrub(extractions)
            warning = "统筹没能合并出规范和清单，规范是各份描述的拼接，不做逐条校验"
            style = StyleSpec(body, (), tuple(sorted(texts)), text_only, warning, degraded=True)
        ctx.state.style = style
        ctx.repo.save_output(
            ctx.session_id,
            table_no=ctx.table_no,
            step=self.name,
            kind="style_spec",
            content=style.to_dict(),
            call_id=parsed.call_id,
        )
        ctx.emit("style_ready", self.name, items=len(style.checklist), text_only=text_only)
        notes = (warning,) if warning else ()
        return StepResult(
            self.name,
            calls=calls,
            degraded=("coordinator",) if style.degraded else (),
            notes=notes,
        )


# --- 风格校验 ------------------------------------------------------------------


@dataclass(frozen=True)
class GateOutcome:
    prompt: str  # 最终采用的提示词
    result: MediaResult  # 最终的生成结果
    passed: bool | None  # None = 没有判定（没有清单 / 没有可用评审者 / 评审无效）
    attempts: int  # 判定过几次
    notes: tuple[str, ...] = ()


def _gate_rows(
    ctx: TableContext, kind: str, sid: str, code: str, phase: int
) -> dict[int, dict[str, Any]]:
    """这个成果在这个阶段（1 = 完成，2 = 按审查修改）已有的判定 / 重画记录，键为第几次。"""
    out: dict[int, dict[str, Any]] = {}
    for o in ctx.repo.outputs(ctx.session_id, table_no=ctx.table_no, kind=kind):
        data = json.loads(o["content"])
        if o["code"] == code and data.get("subtask") == sid and data.get("phase", 1) == phase:
            out[data["attempt"]] = data
    return out


def _review_text(reviewer: str, review: dict[str, Any]) -> str:
    lines = []
    for p in review.get("problems", []):
        lines.append(f"问题：{p['what']}；建议：{p['fix']}")
    for c in review.get("checks", []):
        if not c["ok"]:
            lines.append(f"不符合风格清单：{c['item']}（{c.get('note', '')}）")
    return f'<review from="{reviewer}">\n{neutralize(chr(10).join(lines), MEDIA_TAGS)}\n</review>'


async def _judge(
    ctx: TableContext, sid: str, code: str, attempt: int, prompt: str, res: MediaResult
) -> dict[str, Any] | None:
    """让非作者的 vision 成员对照风格清单判定一次；没有可用评审者时返回 None。"""
    rules = ctx.config.roundtable.media
    seeing = [
        c for c in ctx.active if c != code and "vision" in ctx.models_by_id[ctx.members[c]].tags
    ]
    rng = random.Random(f"{ctx.session_id}:{ctx.table_no}:gate:{sid}:{code}:{attempt}")
    reviewers = shuffled(sorted(seeing), rng)[: rules.gate_reviewers]
    if not reviewers:
        return None
    note, medias = result_note(ctx, "image", res)
    verdicts: list[tuple[str, MediaReview]] = []

    async def one(reviewer: str) -> None:
        parsed = await call_and_parse(
            ctx,
            step=GATE,
            role="member",
            model_id=ctx.members[reviewer],
            prompt=media_review_prompt(ctx, reviewer, "image", prompt, note, medias),
            code=reviewer,
            parse=parse_media_review,
        )
        if parsed.failed_call:
            ctx.drop(reviewer, GATE, "调用失败")
        if parsed.value is not None and parsed.value.valid:
            verdicts.append((reviewer, parsed.value))

    await asyncio.gather(*(one(r) for r in reviewers))
    if not verdicts:
        return None
    verdicts.sort()
    checks: dict[str, dict[str, Any]] = {}
    problems = []
    for _, v in verdicts:
        problems += [{"what": w, "fix": f} for w, f in v.problems]
        for item, ok, note_ in v.checks:
            row = checks.setdefault(item, {"item": item, "ok": True, "note": note_})
            if not ok:
                row.update(ok=False, note=note_)
    passed = all(v.satisfied for _, v in verdicts)
    return {
        "reviewers": [r for r, _ in verdicts],
        "passed": passed,
        "checks": list(checks.values()),
        "problems": problems,
        "checked": "；".join(v.checked for _, v in verdicts if v.checked),
    }


async def style_gate(
    ctx: TableContext,
    *,
    sid: str,
    code: str,
    prompt: str,
    res: MediaResult,
    phase: int = 1,
    subtask_block_text: str = "",
) -> GateOutcome:
    """生成的图片对照风格清单判定；不通过则让作者改提示词重画，最多 style_retries 次。"""
    style = ctx.state.style
    media = ctx.media
    retries = ctx.config.roundtable.media.style_retries
    if style is None or not style.checklist or media is None or not res.ok:
        return GateOutcome(prompt, res, None, 0)
    saved = _gate_rows(ctx, GATE, sid, code, phase)
    redraws = _gate_rows(ctx, "style_redraw", sid, code, phase)
    notes: list[str] = []
    attempt = 1
    while True:
        row = saved.get(attempt)
        if row is None:
            verdict = await _judge(ctx, sid, code, attempt, prompt, res)
            if verdict is None:
                notes.append("没有可用的评审者或评审无效，未做风格校验")
                return GateOutcome(prompt, res, None, attempt - 1, tuple(notes))
            final = verdict["passed"] or attempt > retries
            row = {"subtask": sid, "phase": phase, "attempt": attempt, "final": final, **verdict}
            ctx.repo.save_output(
                ctx.session_id,
                table_no=ctx.table_no,
                step=GATE,
                kind="style_gate",
                code=code,
                content=row,
            )
            saved[attempt] = row
            ctx.emit(
                "style_checked", GATE, code, subtask=sid, attempt=attempt, passed=row["passed"]
            )
        if row["passed"]:
            return GateOutcome(prompt, res, True, attempt, tuple(notes))
        if row["final"]:
            return GateOutcome(prompt, res, False, attempt, tuple(notes))
        # 退回作者重画
        stop = _regen_blocked(ctx, media)
        if stop:
            notes.append(stop)
            return GateOutcome(prompt, res, False, attempt, tuple(notes))
        redraw = redraws.get(attempt)
        if redraw is None:
            new_prompt = await _rewrite(ctx, sid, code, prompt, row, subtask_block_text)
            redraw = {"subtask": sid, "phase": phase, "attempt": attempt, "prompt": new_prompt}
            ctx.repo.save_output(
                ctx.session_id,
                table_no=ctx.table_no,
                step=GATE,
                kind="style_redraw",
                code=code,
                content=redraw,
            )
            redraws[attempt] = redraw
        prompt = redraw["prompt"]
        res = await media.generate(
            "image",
            prompt,
            Placement(ctx.table_no, GATE, phase * 100 + attempt, code, sid),
            tier=ctx.media_tier,
            references=ctx.style_references(),
        )
        if not res.ok:
            notes.append(f"风格不合格后重画失败：{res.error}")
            return GateOutcome(prompt, res, False, attempt, tuple(notes))
        attempt += 1


def _regen_blocked(ctx: TableContext, media) -> str | None:
    """重画受预算与单题确认门槛约束；需要确认或预算不足时不重画（不在并发任务里弹卡片）。"""
    cost = media.estimate("image", ctx.media_tier)
    card: ConfirmationCard | None = ctx.budget_gate(cost)
    if card is not None:
        return "预算不足，没有按风格意见重画"
    if cost > ctx.config.routing.confirm_threshold_usd:
        return "重画的预计花费超过单题确认门槛，没有自动重画"
    return None


async def _rewrite(
    ctx: TableContext,
    sid: str,
    code: str,
    prompt: str,
    row: dict[str, Any],
    subtask_block_text: str,
) -> str:
    """作者根据判定意见改写生成提示词（沿用 rework_media 提示词，意见是对照风格清单的判定）。"""
    template = ctx.prompts.get("rework_media", ctx.prompt_version("rework_media"))
    reviewer = ctx.label(row["reviewers"][0]) if row.get("reviewers") else "评审者"
    block = _review_text(reviewer, row)
    rendered = template.render(
        code=ctx.label(code),
        medium=KIND_LABELS["image"],
        question=ctx.question.text,
        subtask=subtask_block_text or "（没有单独的子任务说明）",
        own_work=prompt,
        reviews_of_you=ctx.scrub(block),
    )
    parsed = await call_and_parse(
        ctx,
        step=GATE,
        role="member",
        model_id=ctx.members[code],
        prompt=rendered,
        code=code,
        parse=parse_revision,
    )
    if parsed.value is not None and parsed.value.answer.strip():
        return parsed.value.answer.strip()
    return (parsed.raw_text or "").strip() or prompt


def retag_failed(ctx: TableContext, sid: str, code: str, outcome: GateOutcome) -> None:
    """重画次数用完仍不通过：记入协同状态，合并时标"风格未通过"。"""
    if outcome.passed is False:
        ctx.state.collab.style_failed.add((sid, code))
    else:
        ctx.state.collab.style_failed.discard((sid, code))


register_step(StyleStep())
