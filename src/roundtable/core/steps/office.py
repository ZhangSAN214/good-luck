"""办公室模式（R17 O2）：经理分诊派活 → 员工办理 → 经理过目（不合格退回改一次）。

- intake：主办经理看题目和在岗员工名单（称呼、擅长、档位、工资标准），决定办事方式、派给谁、
  要不要查资料，并写工作说明。O2 只有单人办：经理建议开会 / 项目分工时照单人办处理并注明。
  经理的输出无法使用或派给了不在名单上的人时，由代码按规则派：能力标签对口（带图片的题需要
  vision）的员工里按预计成本选最便宜的一位。
- handle：被派到的员工按工作说明办理；只有经理判断需要查资料时才提供 search / fetch。
  调用失败时按同样的规则改派一次。
- check：经理过目，通过就交付；退回时员工按意见改一次（step=fix），改后的版本即交付。
  经理的输出无法使用时直接交付员工的成果并注明。

所有员工收到的提示词和参数相同（同类活相同）；成果不让本人审，过目的是经理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from roundtable.core.allocation import NotEnoughModels, cheapest
from roundtable.core.jsonout import extract_json_object

from .base import (
    StepFailed,
    StepResult,
    TableContext,
    call_and_parse,
    call_model,
    neutralize,
    register_step,
)

SEARCH_TOOLS = ("search", "fetch")
MODE_LABELS = {"single": "单人办", "meeting": "开会", "project": "项目分工"}
TAGS = ("question", "brief", "work", "previous", "issues", "staff")


class IntakeOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: Literal["single", "meeting", "project"] = "single"
    assignee: str = Field(min_length=1)
    needs_search: bool = False
    brief: str = ""
    reason: str = ""


class CheckOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    verdict: Literal["pass", "revise"]
    issues: list[str] = Field(default_factory=list)
    note: str = ""


@dataclass
class OfficeState:
    intake: dict[str, Any] | None = None  # 分诊结果（含 assignee 代号、是否由代码兜底）
    work: str | None = None  # 员工办理的成果
    worker: str | None = None  # 实际办理的员工代号（可能被改派）
    check: dict[str, Any] | None = None  # 经理过目
    fixed: str | None = None  # 退回后改的版本

    @property
    def final(self) -> str | None:
        return self.fixed or self.work


def new_office_state() -> OfficeState:
    return OfficeState()


def restore_office(state: Any, kind: str, code: str | None, data: dict[str, Any]) -> bool:
    """从数据库恢复办公室模式的产出；不是办公室模式的产出返回 False。"""
    o = state.office
    if kind == "office_intake":
        o.intake = data
    elif kind == "office_work":
        o.work, o.worker = data["text"], code
    elif kind == "office_check":
        o.check = data
    elif kind == "office_fix":
        o.fixed = data["text"]
    else:
        return False
    return True


def _office(ctx: TableContext) -> OfficeState:
    return ctx.state.office


def _save(ctx: TableContext, step: str, kind: str, content, code=None, call_id=None) -> None:
    ctx.repo.save_output(
        ctx.session_id,
        table_no=ctx.table_no,
        step=step,
        kind=kind,
        code=code,
        content=content,
        call_id=call_id,
    )


def _required_tags(ctx: TableContext) -> set[str]:
    return {"vision"} if any(a.kind == "image" for a in ctx.attachments) else set()


def staff_block(ctx: TableContext) -> str:
    """在岗员工名单（每人一行）：称呼、擅长、档位、工资标准。只看配置，同一题所有经理看到的相同。"""
    labels = ctx.config.personas.tier_labels
    lines = []
    for code in ctx.active:
        spec = ctx.models_by_id[ctx.members[code]]
        tags = "、".join(spec.tags) or "通用"
        tier = labels.get(spec.tier, spec.tier or "")
        lines.append(
            f"- {ctx.label(code)}｜擅长：{tags}｜档位：{tier}"
            f"｜工资：${spec.price.input:g} / ${spec.price.output:g}"
        )
    return neutralize("\n".join(lines), TAGS)


def pick_by_rule(ctx: TableContext, exclude: set[str] = frozenset()) -> str:
    """规则派活：标签对口的员工里按预计成本最便宜的一位（价格相同随机）；没有对口的放宽标签。"""
    pool = [c for c in ctx.active if c not in exclude]
    if not pool:
        raise NotEnoughModels("没有可派的员工")
    specs = {ctx.members[c]: c for c in pool}
    models = [ctx.models_by_id[m] for m in specs]
    need = _required_tags(ctx)
    tagged = [m for m in models if need <= set(m.tags)]
    spec = ctx.config.routing.planner
    chosen = cheapest(
        tagged or models,
        ctx.rng,
        input_tokens=spec.expected_input_tokens,
        output_tokens=spec.expected_output_tokens,
    )
    return specs[chosen.id]


def _caps_text(ctx: TableContext) -> str:
    caps = ctx.config.roundtable.office.task_caps_usd
    return f"单人办 ${caps.single:g}，开会 ${caps.meeting:g}，项目分工 ${caps.project:g}"


class IntakeStep:
    name = "intake"

    async def run(self, ctx: TableContext) -> StepResult:
        o = _office(ctx)
        if o.intake is not None:
            return StepResult(self.name)
        if not ctx.coordinator:
            raise StepFailed("办公室模式需要经理")
        if not ctx.active:
            raise StepFailed("没有在岗的员工")
        prompt = ctx.render(
            "office_intake",
            question=ctx.question.text,
            staff=staff_block(ctx),
            caps=_caps_text(ctx),
        )

        def parse(text: str) -> IntakeOutput | None:
            out = IntakeOutput.model_validate(extract_json_object(text))
            return out if ctx.to_code(out.assignee) in ctx.active else None

        result = await call_and_parse(
            ctx,
            step=self.name,
            role="coordinator",
            model_id=ctx.coordinator,
            prompt=prompt,
            parse=parse,
        )
        notes: list[str] = []
        need = _required_tags(ctx)
        if result.value is not None:
            out: IntakeOutput = result.value
            code = ctx.to_code(out.assignee)
            assert code is not None
            spec = ctx.models_by_id[ctx.members[code]]
            if need <= set(spec.tags):
                intake = {**out.model_dump(), "assignee": code, "by_rule": False}
            else:  # 题目带图片却派给了看不了图的员工：按规则改派
                intake = {
                    **out.model_dump(),
                    "assignee": pick_by_rule(ctx),
                    "by_rule": True,
                    "rule_reason": "原派的员工看不了图片，按规则改派",
                }
        else:
            intake = {
                "mode": "single",
                "assignee": pick_by_rule(ctx),
                "needs_search": False,
                "brief": "",
                "reason": "",
                "by_rule": True,
                "rule_reason": f"经理的分诊无法使用（{result.error or '调用失败'}），按规则派活",
            }
            notes.append(intake["rule_reason"])
        if intake["mode"] != "single":
            notes.append(
                f"经理建议{MODE_LABELS[intake['mode']]}；开会、项目分工还没上线，这次按单人办处理"
            )
        o.intake = intake
        _save(ctx, self.name, "office_intake", intake, call_id=result.call_id)
        ctx.emit("office_assigned", self.name, intake["assignee"], mode=intake["mode"])
        return StepResult(
            self.name,
            calls=1,
            degraded=("coordinator",) if intake["by_rule"] and result.value is None else (),
            notes=tuple(notes),
        )


async def _member_call(ctx: TableContext, step: str, code: str, prompt) -> tuple[str, int]:
    o = _office(ctx)
    search = bool(o.intake and o.intake.get("needs_search"))
    out = await call_model(
        ctx,
        step=step,
        role="member",
        model_id=ctx.members[code],
        prompt=prompt,
        code=code,
        exclude_tools=() if search else SEARCH_TOOLS,
    )
    text = out.completion.text.strip() if out.completion else ""
    return text, out.call_id


def _brief(ctx: TableContext) -> str:
    o = _office(ctx)
    brief = (o.intake or {}).get("brief") or "（经理没有写工作说明，按题目要求办理）"
    return neutralize(ctx.scrub(brief), TAGS)


class HandleStep:
    name = "handle"

    async def run(self, ctx: TableContext) -> StepResult:
        o = _office(ctx)
        if o.work is not None:
            return StepResult(self.name)
        if o.intake is None:
            raise StepFailed("还没有分诊")
        code = o.intake["assignee"]
        tried: set[str] = set()
        notes: list[str] = []
        calls = 0
        while True:
            tried.add(code)
            prompt = ctx.render(
                "office_handle", code=ctx.label(code), question=ctx.question.text, brief=_brief(ctx)
            )
            text, call_id = await _member_call(ctx, self.name, code, prompt)
            calls += 1
            if text:
                break
            ctx.drop(code, self.name, "调用失败或回答为空")
            if len(tried) >= 2:
                raise StepFailed("派到的员工和改派的员工都没有交付成果")
            try:
                failed, code = code, pick_by_rule(ctx, exclude=tried)
            except NotEnoughModels:
                raise StepFailed("派到的员工没有交付成果，也没有可改派的员工") from None
            notes.append(f"{ctx.label(failed)} 调用失败，改派给 {ctx.label(code)}")
        o.work, o.worker = text, code
        _save(ctx, self.name, "office_work", {"text": text}, code=code, call_id=call_id)
        return StepResult(self.name, calls=calls, notes=tuple(notes))


class CheckStep:
    name = "check"

    async def run(self, ctx: TableContext) -> StepResult:
        o = _office(ctx)
        if o.work is None or o.worker is None:
            raise StepFailed("还没有成果可以过目")
        calls = 0
        if o.check is None:
            prompt = ctx.render(
                "office_check",
                question=ctx.question.text,
                brief=_brief(ctx),
                work=neutralize(ctx.scrub(o.work + ctx.member_notes(o.worker)), TAGS),
            )
            result = await call_and_parse(
                ctx,
                step=self.name,
                role="coordinator",
                model_id=ctx.coordinator,
                prompt=prompt,
                parse=lambda text: CheckOutput.model_validate(extract_json_object(text)),
            )
            calls += 1
            if result.value is None:
                check = {
                    "verdict": "pass",
                    "issues": [],
                    "note": "",
                    "degraded": True,
                    "error": result.error or "调用失败",
                }
            else:
                check = {**result.value.model_dump(), "degraded": False}
                if check["verdict"] == "revise" and not check["issues"]:
                    check["verdict"] = "pass"  # 退回却没写问题：没法改，按通过处理
            o.check = check
            _save(ctx, self.name, "office_check", check, call_id=result.call_id)
        notes = []
        if o.check.get("degraded"):
            notes.append(f"经理过目没有完成（{o.check.get('error')}），直接交付员工的成果")
        if o.check["verdict"] == "revise" and o.fixed is None:
            issues = "\n".join(f"{i}. {x}" for i, x in enumerate(o.check["issues"], 1))
            prompt = ctx.render(
                "office_fix",
                code=ctx.label(o.worker),
                question=ctx.question.text,
                brief=_brief(ctx),
                previous=neutralize(o.work, TAGS),
                issues=neutralize(ctx.scrub(issues), TAGS),
            )
            text, call_id = await _member_call(ctx, "fix", o.worker, prompt)
            calls += 1
            if text:
                o.fixed = text
                _save(ctx, "fix", "office_fix", {"text": text}, code=o.worker, call_id=call_id)
            else:
                notes.append("员工修改时调用失败，交付修改前的版本")
        return StepResult(self.name, calls=calls, notes=tuple(notes))


register_step(IntakeStep())
register_step(HandleStep())
register_step(CheckStep())
