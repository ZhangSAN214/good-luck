"""办公室模式（R17 O2、O3）：经理分诊派活 → 员工办理 → 经理过目（不合格退回改一次）；或者开会。

- intake：主办经理看题目和在岗员工名单（称呼、擅长、档位、工资标准），决定办事方式、派给谁、
  要不要查资料，并写工作说明。项目分工（O4）上线前，经理建议项目分工时照单人办处理并注明。
  经理的输出无法使用或派给了不在名单上的人时，由代码按规则派：能力标签对口（带图片的题需要
  vision）的员工里按预计成本选最便宜的一位。
  开会时经理点名 2～3 位参会员工（office.meeting）；点名不够或点了名单外的人时按同样的规则补齐。
  预计开会花费超过「开会」工资上限时先弹卡片（开会 / 改单人办 / 停止）。
  项目分工时（O4）经理点名 2～4 人的项目组（office.project）；预计花费超过「项目分工」上限时
  同样先弹卡片（开工 / 改单人办 / 停止）。确认后本桌的 handle、check 跳过，由编排引擎另开一张
  「项目组」桌子：只有项目组成员上桌，主办经理统筹，走 office.project.pipeline（协同流水线，
  交叉审查只找 1 位，合并用中等思考强度），最后 office_review 由另一位经理验收（退回改一次）。
- handle：单人办时被派到的员工按工作说明办理；只有经理判断需要查资料时才提供 search / fetch。
  调用失败时按同样的规则改派一次。
  开会时（step=speak）参会员工按随机顺序各发言一次，后发言的看得到前面的发言（像真实会议）；
  然后经理拍板（step=decide），写出结论和会议纪要，结论即交付。开会不提供工具。
- check：经理过目，通过就交付；退回时员工按意见改一次（step=fix），改后的版本即交付。
  经理的输出无法使用时直接交付员工的成果并注明。开会的结论是经理自己拍板的，不再过目。

所有员工收到的提示词和参数相同（同类活相同）；成果不让本人审，过目的是经理；经理不参会发言。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from roundtable.core.allocation import NotEnoughModels, cheapest, shuffled
from roundtable.core.cards import CardOption, ConfirmationCard, money
from roundtable.core.jsonout import extract_json_object
from roundtable.core.providers.cost import estimate_cost

from .base import (
    NeedsApproval,
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
TAGS = ("question", "brief", "work", "previous", "issues", "staff", "speeches", "speech")


class IntakeOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mode: Literal["single", "meeting", "project"] = "single"
    assignee: str = Field(min_length=1)
    attendees: list[str] = Field(default_factory=list)
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
    speeches: dict[str, str] = field(default_factory=dict)  # 开会：代号 → 发言（按发言顺序）
    decision: dict[str, Any] | None = None  # 开会：经理拍板（text、degraded）

    @property
    def meeting(self) -> bool:
        return bool(self.intake and self.intake.get("mode") == "meeting")

    @property
    def project(self) -> bool:
        return bool(self.intake and self.intake.get("mode") == "project")

    @property
    def final(self) -> str | None:
        if self.project:  # 成果在项目组那张桌子上
            return None
        if self.meeting:
            return self.decision["text"] if self.decision else None
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
    elif kind == "office_review":
        o.check = data
    elif kind == "office_revised":
        o.fixed = data["text"]
    elif kind == "office_approval":
        o.intake = {**(o.intake or {}), **data}
    elif kind == "office_speech":
        o.speeches[code or ""] = data["text"]
    elif kind == "office_decision":
        o.decision = data
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


def _usable(ctx: TableContext, code: str) -> bool:
    """员工能不能接这件事：题目带图片时要看得了图。"""
    return _required_tags(ctx) <= set(ctx.models_by_id[ctx.members[code]].tags)


GROUP_MODES = ("meeting", "project")


def group_size(ctx: TableContext, mode: str) -> tuple[int, int]:
    """开会的参会人数 / 项目组人数（最少、最多）。"""
    office = ctx.config.roundtable.office
    if mode == "meeting":
        return office.meeting.min_attendees, office.meeting.max_attendees
    return office.project.min_members, office.project.max_members


def pick_attendees(
    ctx: TableContext, named: list[str], low: int, high: int
) -> tuple[list[str], bool]:
    """开会的参会员工 / 项目组：经理点名的（在名单上、能接这件事、不重复，最多 high 位），
    不够 low 位时按规则补齐。返回 (代号列表, 是否由代码补过)。"""
    chosen: list[str] = []
    for name in named:
        code = ctx.to_code(name)
        if code in ctx.active and code not in chosen and _usable(ctx, code):
            chosen.append(code)
    chosen = chosen[:high]
    filled = False
    while len(chosen) < low:
        try:
            extra = pick_by_rule(ctx, exclude=set(chosen))
        except NotEnoughModels:
            break
        if not _usable(ctx, extra) and any(
            _usable(ctx, c) for c in ctx.active if c not in chosen
        ):  # pick_by_rule 放宽了标签：还有看得了图的人时不要看不了图的
            break
        chosen.append(extra)
        filled = True
    return chosen, filled


def meeting_estimate(ctx: TableContext, attendees: list[str]) -> float:
    """开会的预计花费：每位员工发言一次（输入含前面的发言）+ 经理拍板一次。按配置价格公式估计。"""
    from roundtable.core.routing.estimate import text_tokens

    params = ctx.config.routing.estimate
    o = params.prompt_overhead_tokens + params.intake_tokens
    q = text_tokens(ctx.question.text, params)
    q += sum(params.image_tokens for a in ctx.attachments if a.kind == "image")
    caps = {s: ctx.step_params(s).get("max_tokens") for s in ("speak", "decide")}

    def cost(model_id: str, step: str, tin: int, tout: int) -> float:
        spec = ctx.models_by_id[model_id]
        usable = ctx.router.plan(model_id).usable
        price = spec.price_for(usable[0]) if usable else spec.price
        tout = round(tout * params.output_multiplier.get(spec.tier or "", 1.0))
        return estimate_cost(price, tin, min(tout, caps[step] or tout))

    total = sum(
        cost(ctx.members[c], "speak", o + q + i * params.speech_tokens, params.speech_tokens)
        for i, c in enumerate(attendees)
    )
    assert ctx.coordinator is not None
    tin = o + q + len(attendees) * params.speech_tokens
    return total + cost(ctx.coordinator, "decide", tin, params.minutes_tokens)


def group_key(ctx: TableContext) -> str:
    return f"office_group:{ctx.table_no}"


def project_lineup(config, members: list[str], coordinator: str):
    """项目组桌子的阵容：只有项目组成员上桌，主办经理统筹，走 office.project.pipeline。"""
    from roundtable.core.routing import Lineup

    return Lineup(tuple(members), coordinator, tuple(config.roundtable.office.project.pipeline))


def project_config(config):
    """项目组桌子上生效的配置：每份成果的审查人数、覆盖的步骤参数（用于预估，与实际执行一致）。"""
    project = config.roundtable.office.project
    params = dict(config.roundtable.step_params)
    for step, override in project.step_params.items():
        params[step] = {**params.get(step, {}), **override}
    rt = config.roundtable.model_copy(
        update={"reviews_per_answer": project.reviews_per_item, "step_params": params}
    )
    return config.model_copy(update={"roundtable": rt})


def estimate_project(config, router, question, members: list[str], coordinator: str):
    """项目组桌子的预计花费（CostEstimate），按默认难度估。"""
    from roundtable.core.routing import Assessment, estimate_lineup

    return estimate_lineup(
        project_lineup(config, members, coordinator),
        question,
        Assessment(difficulty=None, source="default"),
        config=project_config(config),
        router=router,
    )


def project_estimate(ctx: TableContext, team: list[str]):
    assert ctx.coordinator is not None
    members = [ctx.members[c] for c in team]
    return estimate_project(ctx.config, ctx.router, ctx.question, members, ctx.coordinator)


class IntakeStep:
    name = "intake"

    async def run(self, ctx: TableContext) -> StepResult:
        o = _office(ctx)
        calls = 0
        notes: list[str] = []
        degraded: tuple[str, ...] = ()
        if o.intake is None:
            calls, notes, degraded = await self._triage(ctx)
        if o.intake["mode"] in GROUP_MODES and not o.intake.get("approved"):
            notes += self._group_gate(ctx)
        return StepResult(self.name, calls=calls, degraded=degraded, notes=tuple(notes))

    async def _triage(self, ctx: TableContext) -> tuple[int, list[str], tuple[str, ...]]:
        o = _office(ctx)
        if not ctx.coordinator:
            raise StepFailed("办公室模式需要经理")
        if not ctx.active:
            raise StepFailed("没有在岗的员工")

        def span(mode: str) -> str:
            low, high = group_size(ctx, mode)
            return str(low) if low == high else f"{low}～{high}"

        prompt = ctx.render(
            "office_intake",
            question=ctx.question.text,
            staff=staff_block(ctx),
            caps=_caps_text(ctx),
            attendees=span("meeting"),
            team=span("project"),
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
        if result.value is not None:
            out: IntakeOutput = result.value
            code = ctx.to_code(out.assignee)
            assert code is not None
            if _usable(ctx, code):
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
        intake["attendees"] = []
        if intake["mode"] in GROUP_MODES:
            low, high = group_size(ctx, intake["mode"])
            named = result.value.attendees if result.value else []
            chosen, filled = pick_attendees(ctx, named, low, high)
            what = MODE_LABELS[intake["mode"]]
            if len(chosen) < low:
                notes.append(f"在岗员工不够 {low} 位，没法{what}，这次按单人办处理")
                intake["mode"] = "single"
            else:
                intake["attendees"] = chosen
                if filled:
                    intake["attendees_by_rule"] = True
                    notes.append(f"经理点名的人不够{what}，按规则补齐")
        o.intake = intake
        _save(ctx, self.name, "office_intake", intake, call_id=result.call_id)
        ctx.emit(
            "office_assigned",
            self.name,
            intake["assignee"],
            mode=intake["mode"],
            attendees=list(intake["attendees"]),
        )
        degraded = ("coordinator",) if intake["by_rule"] and result.value is None else ()
        return 1, notes, degraded

    def _group_gate(self, ctx: TableContext) -> list[str]:
        """开会 / 项目分工的预计花费：预算不够时弹预算卡片；超过该方式的工资上限时问老板
        是照办还是改单人办。"""
        o = _office(ctx)
        assert o.intake is not None
        mode, attendees = o.intake["mode"], o.intake["attendees"]
        caps = ctx.config.roundtable.office.task_caps_usd
        if mode == "meeting":
            cost, cap = meeting_estimate(ctx, attendees), caps.meeting
        else:
            cost, cap = project_estimate(ctx, attendees).total_usd, caps.project
        budget_card = ctx.budget_gate(cost)
        if budget_card is not None:
            raise NeedsApproval(budget_card)
        answer = ctx.approval(group_key(ctx)) if cost > cap else mode
        if answer in (mode, "single"):
            # 结果单独存一条（office_approval），恢复时叠加到分诊结果上
            patch: dict[str, Any] = {"approved": True, f"{mode}_estimate_usd": cost}
            if answer == "single":
                patch.update(mode="single", attendees=[], declined=mode)
            o.intake = {**o.intake, **patch}
            _save(ctx, self.name, "office_approval", patch)
            if answer == "single":
                return [f"老板选择不{MODE_LABELS[mode]}，改为单人办"]
            return []
        names = "、".join(ctx.label(c) for c in attendees)
        single = ctx.label(o.intake["assignee"])
        if mode == "meeting":
            plan = f"经理建议开会：{names} 依次发言，经理拍板"
            go = CardOption("meeting", "开会", cost, "按预估计费，实际以渠道返回为准")
        else:
            plan = f"经理建议项目分工：{names} 组成项目组，经理拆分、合并，另一位经理验收"
            go = CardOption("project", "开工", cost, "按预估计费，实际以渠道返回为准")
        card = ConfirmationCard(
            kind="office",
            situation=(
                f"{plan}，预计 {money(cost)}，超过{MODE_LABELS[mode]}的工资上限 {money(cap)}。"
            ),
            options=(
                go,
                CardOption("single", "改单人办", 0.0, f"只请 {single} 办理，经理过目"),
                CardOption("stop", "停止", 0.0, "不再继续"),
            ),
            recommendation=mode,
            reason=o.intake.get("reason") or "经理判断这件事需要几个人一起做",
        )
        raise NeedsApproval(card, group_key(ctx))


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
        if o.meeting:
            return await run_meeting(ctx, self.name)
        if o.project:  # 项目分工：交给另开的项目组桌子
            return StepResult(self.name)
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
        if o.meeting or o.project:  # 开会的结论是经理自己拍板的；项目由另一位经理验收
            return StepResult(self.name)
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


def _speeches_block(ctx: TableContext, speeches: dict[str, str]) -> str:
    """已有的发言（按发言顺序）。每段放在 <speech> 标签内，标明发言人称呼。"""
    parts = [
        f'<speech from="{ctx.label(code)}">\n{neutralize(ctx.scrub(text), TAGS)}\n</speech>'
        for code, text in speeches.items()
    ]
    return "\n\n".join(parts)


async def run_meeting(ctx: TableContext, step: str) -> StepResult:
    """开会：参会员工按随机顺序各发言一次（后发言的看得到前面的发言）→ 经理拍板写结论和纪要。

    中途恢复时已经发过言的不再调用；调用失败的员工跳过（注明），全部失败时步骤失败。
    """
    o = _office(ctx)
    assert o.intake is not None
    notes: list[str] = []
    calls = 0
    # 发言顺序随机（由会话 seed 派生，可复现）；已经发过言的排在前面，保持原顺序
    order = [c for c in o.speeches] + [
        c for c in shuffled(o.intake["attendees"], ctx.rng) if c not in o.speeches
    ]
    for code in order:
        if code in o.speeches or code in ctx.state.dropped:
            continue
        prompt = ctx.render(
            "office_speak",
            code=ctx.label(code),
            question=ctx.question.text,
            brief=_brief(ctx),
            speeches=_speeches_block(ctx, o.speeches),
        )
        out = await call_model(
            ctx, step="speak", role="member", model_id=ctx.members[code], prompt=prompt, code=code
        )
        calls += 1
        text = out.completion.text.strip() if out.completion else ""
        if not text:
            ctx.drop(code, "speak", "调用失败或发言为空")
            notes.append(f"{ctx.label(code)} 没能发言（调用失败），会议继续")
            continue
        o.speeches[code] = text
        _save(ctx, "speak", "office_speech", {"text": text}, code=code, call_id=out.call_id)
    if not o.speeches:
        raise StepFailed("参会的员工都没能发言")
    if o.decision is None:
        prompt = ctx.render(
            "office_decide",
            question=ctx.question.text,
            brief=_brief(ctx),
            speeches=_speeches_block(ctx, o.speeches),
        )
        assert ctx.coordinator is not None
        out = await call_model(
            ctx, step="decide", role="coordinator", model_id=ctx.coordinator, prompt=prompt
        )
        calls += 1
        text = out.completion.text.strip() if out.completion else ""
        if text:
            o.decision = {"text": text, "degraded": False}
        else:
            # 经理没能拍板：把各位的发言原样交给老板，并注明
            lines = ["经理没能写出结论，以下是各位员工的发言，请你自己判断。"]
            lines += [f"### {ctx.label(c)}\n\n{t}" for c, t in o.speeches.items()]
            o.decision = {"text": "\n\n".join(lines), "degraded": True}
            notes.append("经理拍板时调用失败，直接交付各位员工的发言")
        _save(ctx, "decide", "office_decision", o.decision, call_id=out.call_id)
    return StepResult(step, calls=calls, notes=tuple(notes))


def pick_reviewer(ctx: TableContext) -> str | None:
    """复核经理：主办经理以外、当前能用的另一位经理（office_role: manager）；多位时按 seed 随机。"""
    others = [
        m.id
        for m in ctx.router.available_models()
        if m.office_role == "manager" and m.seat and m.id != ctx.coordinator
    ]
    return ctx.rng.choice(sorted(others)) if others else None


class OfficeReviewStep:
    """项目组桌子的最后一步：另一位经理验收主办经理合并好的成果，退回时主办经理改一次。"""

    name = "office_review"

    async def run(self, ctx: TableContext) -> StepResult:
        o = _office(ctx)
        merge = ctx.state.collab.merge
        if merge is None:
            raise StepFailed("还没有合并好的成果可以验收")
        result_text = merge.output.result
        calls = 0
        notes: list[str] = []
        if o.check is None:
            reviewer = pick_reviewer(ctx)
            if reviewer is None:
                o.check = {"verdict": "pass", "issues": [], "note": "", "skipped": True}
                _save(ctx, self.name, "office_review", o.check)
                return StepResult(self.name, notes=("没有第二位经理可以复核，直接交付合并的成果",))
            prompt = ctx.render(
                "office_review",
                question=ctx.question.text,
                result=neutralize(ctx.scrub(result_text), TAGS + ("result",)),
            )
            parsed = await call_and_parse(
                ctx,
                step=self.name,
                role="reviewer",
                model_id=reviewer,
                prompt=prompt,
                parse=lambda text: CheckOutput.model_validate(extract_json_object(text)),
            )
            calls += 1
            if parsed.value is None:
                check = {
                    "verdict": "pass",
                    "issues": [],
                    "note": "",
                    "degraded": True,
                    "error": parsed.error or "调用失败",
                }
            else:
                check = {**parsed.value.model_dump(), "degraded": False}
                if check["verdict"] == "revise" and not check["issues"]:
                    check["verdict"] = "pass"
            o.check = check
            _save(ctx, self.name, "office_review", check, call_id=parsed.call_id)
        if o.check.get("degraded"):
            notes.append(f"复核经理验收没有完成（{o.check.get('error')}），直接交付合并的成果")
        if o.check["verdict"] == "revise" and o.fixed is None:
            assert ctx.coordinator is not None
            issues = "\n".join(f"{i}. {x}" for i, x in enumerate(o.check["issues"], 1))
            prompt = ctx.render(
                "office_revise",
                question=ctx.question.text,
                result=neutralize(result_text, TAGS + ("result",)),
                issues=neutralize(ctx.scrub(issues), TAGS),
            )
            out = await call_model(
                ctx,
                step="office_revise",
                role="coordinator",
                model_id=ctx.coordinator,
                prompt=prompt,
            )
            calls += 1
            text = out.completion.text.strip() if out.completion else ""
            if text:
                o.fixed = text
                _save(ctx, "office_revise", "office_revised", {"text": text}, call_id=out.call_id)
            else:
                notes.append("主办经理修改时调用失败，交付修改前的版本")
        return StepResult(self.name, calls=calls, notes=tuple(notes))


register_step(IntakeStep())
register_step(HandleStep())
register_step(CheckStep())
register_step(OfficeReviewStep())
