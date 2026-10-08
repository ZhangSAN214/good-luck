"""成本优先路由的入口：判断难度 → 选方案 → 组阵容 → 预估花费 → 是否需要确认；以及升级。

用户的选择永远优先：
- auto（默认）：规则判断 → 规则判断不出时调用规划员 → 按难度选方案；
- preset：直接使用预设对应的方案，只做零成本的规则判断用于记录；
- manual：用户勾选的组员原样上桌（统筹可指定），只做规则判断用于记录。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Literal

from roundtable.core.allocation import NotEnoughModels
from roundtable.core.config import AppConfig, ModelSpec, Price
from roundtable.core.config.schema import Confidence, Difficulty, EscalationRule
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import ChannelRouter

from .estimate import CostEstimate, estimate_pipeline, text_tokens
from .lineup import Lineup, LineupBuilder
from .planner import PlannerResult, run_planner
from .triage import Question, triage

Mode = Literal["auto", "preset", "manual"]
Source = Literal["rule", "model", "default", "skipped"]


class RoutingError(ValueError):
    """用户选择无效，或可用模型无法满足方案。"""


@dataclass(frozen=True)
class UserChoice:
    mode: Mode = "auto"
    preset: str | None = None
    members: tuple[str, ...] = ()
    coordinator: str | None = None

    def __post_init__(self) -> None:
        if self.mode == "preset" and not self.preset:
            raise RoutingError("预设模式需要指定预设")
        if self.mode == "manual" and not self.members:
            raise RoutingError("手动模式至少需要勾选一个模型")
        if self.mode != "preset" and self.preset:
            raise RoutingError("只有预设模式可以指定预设")
        if self.mode != "manual" and (self.members or self.coordinator):
            raise RoutingError("只有手动模式可以勾选模型")
        if len(set(self.members)) != len(self.members):
            raise RoutingError("勾选的模型不能重复")
        if self.coordinator and self.coordinator in self.members:
            raise RoutingError("统筹不能兼任组员")


@dataclass(frozen=True)
class Assessment:
    difficulty: Difficulty | None
    source: Source
    task_type: str | None = None
    require_tags: tuple[str, ...] = ()
    rules_matched: tuple[str, ...] = ()
    reason: str = ""
    expected_answer_tokens: int | None = None
    planner: PlannerResult | None = None


@dataclass(frozen=True)
class PlanOption:
    label: str
    available: bool
    estimate: CostEstimate | None = None
    reason: str = ""


@dataclass(frozen=True)
class RoutingDecision:
    seed: int
    choice: UserChoice
    assessment: Assessment
    plan: str | None  # 手动模式为 None
    lineup: Lineup
    estimate: CostEstimate
    options: dict[str, PlanOption]
    escalate_to: str | None
    confirm_threshold_usd: float
    warnings: tuple[str, ...] = ()
    escalated_from: str | None = None
    escalation_reason: str | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.estimate.total_usd > self.confirm_threshold_usd

    @property
    def planner_cost_usd(self) -> float:
        p = self.assessment.planner
        return p.cost_usd if p else 0.0

    def record(self, question: Question) -> RoutingRecord:
        a = self.assessment
        return RoutingRecord(
            seed=self.seed,
            question_chars=len(question.text),
            attachments=question.attachments,
            mode=self.choice.mode,
            preset=self.choice.preset,
            difficulty=a.difficulty,
            difficulty_source=a.source,
            task_type=a.task_type,
            require_tags=a.require_tags,
            rules_matched=a.rules_matched,
            assessment_reason=a.reason,
            expected_answer_tokens=a.expected_answer_tokens,
            planner_model=a.planner.model_id if a.planner else None,
            planner_cost_usd=self.planner_cost_usd,
            planner_error=a.planner.error if a.planner else None,
            plan=self.plan,
            members=self.lineup.members,
            coordinator=self.lineup.coordinator,
            estimated_cost_usd=self.estimate.total_usd,
            needs_confirmation=self.needs_confirmation,
            options={
                k: (v.estimate.total_usd if v.estimate else None) for k, v in self.options.items()
            },
        )


@dataclass(frozen=True)
class RoutingRecord:
    """每题一条，供以后调整规则：难度判断、预估与实际花费、是否升级。"""

    seed: int
    question_chars: int
    attachments: tuple[str, ...]
    mode: Mode
    preset: str | None
    difficulty: Difficulty | None
    difficulty_source: Source
    task_type: str | None
    require_tags: tuple[str, ...]
    rules_matched: tuple[str, ...]
    assessment_reason: str
    expected_answer_tokens: int | None
    planner_model: str | None
    planner_cost_usd: float
    planner_error: str | None
    plan: str | None
    members: tuple[str, ...]
    coordinator: str | None
    estimated_cost_usd: float
    needs_confirmation: bool
    options: dict[str, float | None]
    # 以下在讨论结束后填写
    actual_cost_usd: float | None = None
    escalated: bool = False
    escalation_reason: str | None = None
    escalated_plan: str | None = None
    escalation_estimated_cost_usd: float | None = None
    user_confirmed: bool | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def with_outcome(
        self,
        *,
        actual_cost_usd: float,
        escalation: RoutingDecision | None = None,
        user_confirmed: bool | None = None,
    ) -> RoutingRecord:
        return replace(
            self,
            actual_cost_usd=actual_cost_usd,
            escalated=escalation is not None,
            escalation_reason=escalation.escalation_reason if escalation else None,
            escalated_plan=escalation.plan if escalation else None,
            escalation_estimated_cost_usd=escalation.estimate.total_usd if escalation else None,
            user_confirmed=user_confirmed,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def assessment(self) -> Assessment:
        """从记录重建难度判断（不含规划员调用详情），供恢复与升级使用。"""
        return Assessment(
            difficulty=self.difficulty,
            source=self.difficulty_source,
            task_type=self.task_type,
            require_tags=tuple(self.require_tags),
            rules_matched=tuple(self.rules_matched),
            reason=self.assessment_reason,
            expected_answer_tokens=self.expected_answer_tokens,
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RoutingRecord:
        data = dict(d)
        for key in ("attachments", "require_tags", "rules_matched", "members"):
            data[key] = tuple(data.get(key) or ())
        return cls(**data)


# --- 内部工具 --------------------------------------------------------------------


def _route_price(router: ChannelRouter, model: ModelSpec) -> Price:
    """该模型当前会首先使用的渠道的价格。"""
    usable = router.plan(model.id).usable
    return model.price_for(usable[0]) if usable else model.price


def _answer_tokens(config: AppConfig, a: Assessment, difficulty: Difficulty) -> int:
    params = config.routing.estimate
    if a.expected_answer_tokens:
        b = params.answer_tokens_bounds
        return max(b.min, min(b.max, a.expected_answer_tokens))
    return params.answer_tokens[difficulty]


def _estimate(
    lineup: Lineup,
    *,
    config: AppConfig,
    router: ChannelRouter,
    by_id: dict[str, ModelSpec],
    question: Question,
    answer_tokens: int,
) -> CostEstimate:
    params = config.routing.estimate
    return estimate_pipeline(
        lineup.pipeline,
        member_prices=[_route_price(router, by_id[m]) for m in lineup.members],
        coordinator_price=(
            _route_price(router, by_id[lineup.coordinator]) if lineup.coordinator else None
        ),
        question_tokens=text_tokens(question.text, params),
        answer_tokens=answer_tokens,
        revise_rounds=config.roundtable.revise_rounds,
        params=params,
    )


def estimate_lineup(
    lineup: Lineup,
    question: Question,
    assessment: Assessment,
    *,
    config: AppConfig,
    router: ChannelRouter,
) -> CostEstimate:
    """按实际阵容估算花费（公开给编排引擎使用）。"""
    available = {m.id: m for m in config.models.models}
    difficulty = assessment.difficulty or config.routing.default_difficulty
    return _estimate(
        lineup,
        config=config,
        router=router,
        by_id=available,
        question=question,
        answer_tokens=_answer_tokens(config, assessment, difficulty),
    )


def option_lineup(
    plan_name: str,
    assessment: Assessment,
    *,
    seed: int,
    config: AppConfig,
    router: ChannelRouter,
) -> Lineup:
    """重建确认卡片上某个备选方案的阵容（与估价时使用同一个随机种子，结果一致）。"""
    builder = LineupBuilder(
        config,
        router.available_models(),
        random.Random(f"{seed}:{plan_name}"),
        required_tags=assessment.require_tags,
        task_type=assessment.task_type,
    )
    try:
        return builder.build(config.routing.plans[plan_name])
    except NotEnoughModels as exc:
        raise RoutingError(str(exc)) from None


async def _assess(
    question: Question,
    choice: UserChoice,
    *,
    config: AppConfig,
    router: ChannelRouter,
    prompts: PromptLibrary,
    available: Sequence[ModelSpec],
    rng: random.Random,
) -> Assessment:
    rules = triage(question, config.routing.triage)
    base = Assessment(
        difficulty=rules.difficulty,
        source="rule" if rules.difficulty else "skipped",
        task_type=rules.task_type,
        require_tags=rules.require_tags,
        rules_matched=rules.matched,
        reason=f"规则 {rules.difficulty_rule}" if rules.difficulty_rule else "",
    )
    if rules.difficulty is not None or choice.mode != "auto":
        return base

    planner = await run_planner(
        question.text,
        config=config,
        router=router,
        prompts=prompts,
        available=available,
        rng=rng,
    )
    if planner.ok:
        out = planner.output
        return replace(
            base,
            difficulty=out.difficulty,
            source="model",
            task_type=base.task_type or out.task_type,
            reason=out.reason,
            expected_answer_tokens=out.expected_answer_tokens,
            planner=planner,
        )
    return replace(
        base,
        difficulty=config.routing.default_difficulty,
        source="default",
        reason=f"规划员不可用，使用默认难度（{planner.error}）",
        planner=planner,
    )


def _options(
    question: Question,
    assessment: Assessment,
    *,
    config: AppConfig,
    router: ChannelRouter,
    available: Sequence[ModelSpec],
    seed: int,
) -> dict[str, PlanOption]:
    """所有方案的预估花费，供界面展示和用户切换。用独立的随机数，不影响实际抽取。"""
    by_id = {m.id: m for m in available}
    options = {}
    for name, plan in config.routing.plans.items():
        builder = LineupBuilder(
            config,
            available,
            random.Random(f"{seed}:{name}"),
            required_tags=assessment.require_tags,
            task_type=assessment.task_type,
        )
        difficulty = assessment.difficulty or config.routing.default_difficulty
        try:
            lineup = builder.build(plan)
        except NotEnoughModels as exc:
            options[name] = PlanOption(plan.label, False, None, str(exc))
            continue
        estimate = _estimate(
            lineup,
            config=config,
            router=router,
            by_id=by_id,
            question=question,
            answer_tokens=_answer_tokens(config, assessment, difficulty),
        )
        options[name] = PlanOption(plan.label, True, estimate)
    return options


# --- 入口 --------------------------------------------------------------------------


async def route_question(
    question: Question,
    choice: UserChoice | None = None,
    *,
    config: AppConfig,
    router: ChannelRouter,
    prompts: PromptLibrary,
    seed: int,
    recent_coordinators: Sequence[str] = (),
) -> RoutingDecision:
    choice = choice or UserChoice()
    rng = random.Random(seed)
    available = router.available_models()
    by_id = {m.id: m for m in available}
    routing = config.routing
    warnings: list[str] = []

    if choice.mode == "preset" and choice.preset not in routing.presets:
        raise RoutingError(f"未知的预设 {choice.preset!r}，可选：{sorted(routing.presets)}")
    if choice.mode == "manual":
        unknown = [m for m in (*choice.members, choice.coordinator) if m and m not in by_id]
        if unknown:
            raise RoutingError(f"以下模型不可用（未启用或没有可用渠道）：{unknown}")
        if len(choice.members) > config.roundtable.seats:
            raise RoutingError(f"最多勾选 {config.roundtable.seats} 个组员")

    assessment = await _assess(
        question,
        choice,
        config=config,
        router=router,
        prompts=prompts,
        available=available,
        rng=rng,
    )
    builder = LineupBuilder(
        config,
        available,
        rng,
        required_tags=assessment.require_tags,
        task_type=assessment.task_type,
        recent_coordinators=recent_coordinators,
    )

    plan_name: str | None
    escalate_to: str | None
    try:
        if choice.mode == "manual":
            plan_name, escalate_to = None, None
            lineup = builder.build_manual(
                choice.members, choice.coordinator, routing.manual.coordinator
            )
            missing = [
                m for m in choice.members if not set(assessment.require_tags) <= set(by_id[m].tags)
            ]
            if missing:
                warnings.append(
                    f"按题目需要标签 {list(assessment.require_tags)}，但所选的 {missing} 不具备；"
                    "按你的选择执行"
                )
        else:
            if choice.mode == "preset":
                preset = routing.presets[choice.preset]
                plan_name = preset.plan
                escalate_to = routing.plans[plan_name].escalate_to if preset.escalate else None
            else:
                plan_name = routing.difficulty_plans[assessment.difficulty]
                escalate_to = routing.plans[plan_name].escalate_to
            lineup = builder.build(routing.plans[plan_name])
    except NotEnoughModels as exc:
        raise RoutingError(str(exc)) from None

    difficulty = assessment.difficulty or routing.default_difficulty
    estimate = _estimate(
        lineup,
        config=config,
        router=router,
        by_id=by_id,
        question=question,
        answer_tokens=_answer_tokens(config, assessment, difficulty),
    )
    options = _options(
        question, assessment, config=config, router=router, available=available, seed=seed
    )
    if plan_name is not None:  # 已选方案按实际阵容估价，与执行时一致
        options[plan_name] = PlanOption(routing.plans[plan_name].label, True, estimate)
    return RoutingDecision(
        seed=seed,
        choice=choice,
        assessment=assessment,
        plan=plan_name,
        lineup=lineup,
        estimate=estimate,
        options=options,
        escalate_to=escalate_to,
        confirm_threshold_usd=routing.confirm_threshold_usd,
        warnings=tuple(warnings),
    )


# --- 升级 --------------------------------------------------------------------------


@dataclass(frozen=True)
class OutcomeSignals:
    """来自汇总结果的信号。"""

    unresolved_disagreements: int
    confidence: Confidence | None = None


def escalation_reason(signals: OutcomeSignals, rule: EscalationRule) -> str | None:
    reasons = []
    if signals.unresolved_disagreements >= rule.min_disagreements:
        reasons.append(f"未解决的分歧 {signals.unresolved_disagreements} 处")
    if signals.confidence in rule.confidence:
        reasons.append(f"把握程度 {signals.confidence}")
    return "；".join(reasons) or None


def plan_escalation(
    decision: RoutingDecision,
    question: Question,
    signals: OutcomeSignals,
    *,
    config: AppConfig,
    router: ChannelRouter,
    recent_coordinators: Sequence[str] = (),
) -> RoutingDecision | None:
    """满足升级条件时，返回升级后的新决定（重新组建阵容、重新预估）；否则返回 None。

    升级后的圆桌独立重新作答，不沿用之前的答案，避免被低档位的结论带偏。
    """
    return escalate(
        seed=decision.seed,
        choice=decision.choice,
        assessment=decision.assessment,
        from_plan=decision.plan,
        escalate_to=decision.escalate_to,
        question=question,
        signals=signals,
        config=config,
        router=router,
        recent_coordinators=recent_coordinators,
    )


def escalate(
    *,
    seed: int,
    choice: UserChoice,
    assessment: Assessment,
    from_plan: str | None,
    escalate_to: str | None,
    question: Question,
    signals: OutcomeSignals,
    config: AppConfig,
    router: ChannelRouter,
    recent_coordinators: Sequence[str] = (),
) -> RoutingDecision | None:
    """与 plan_escalation 相同，但只需要存库的信息（恢复执行后也能用）。"""
    if escalate_to is None:
        return None
    reason = escalation_reason(signals, config.routing.escalation)
    if reason is None:
        return None
    plan = config.routing.plans[escalate_to]
    builder = LineupBuilder(
        config,
        router.available_models(),
        random.Random(f"{seed}:escalate:{escalate_to}"),
        required_tags=assessment.require_tags,
        task_type=assessment.task_type,
        recent_coordinators=recent_coordinators,
    )
    try:
        lineup = builder.build(plan)
    except NotEnoughModels as exc:
        raise RoutingError(f"无法升级到 {escalate_to}：{exc}") from None
    estimate = estimate_lineup(lineup, question, assessment, config=config, router=router)
    return RoutingDecision(
        seed=seed,
        choice=choice,
        assessment=assessment,
        plan=escalate_to,
        lineup=lineup,
        estimate=estimate,
        options={escalate_to: PlanOption(plan.label, True, estimate)},
        escalate_to=plan.escalate_to,
        confirm_threshold_usd=config.routing.confirm_threshold_usd,
        escalated_from=from_plan,
        escalation_reason=reason,
    )
