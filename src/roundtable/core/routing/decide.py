"""路由入口：估计答案长度 → 组阵容（所选档位全员上桌）→ 预估花费 → 是否需要确认；以及升级。

用户的选择永远优先：
- 档位（plans 中的名称，如 budget / flagship）：该档位所有可用模型上桌；
- custom（自选）：勾选的模型全部上桌，统筹可指定。
难度判断（规则，规则判断不出时调用规划员）只用于估计答案长度，不影响谁上桌。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any, Literal

from roundtable.core.allocation import NotEnoughModels
from roundtable.core.config import AppConfig, ModelSpec, Price
from roundtable.core.config.schema import Confidence, Difficulty, EscalationRule
from roundtable.core.media import KINDS
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import ChannelRouter

from .estimate import CostEstimate, EstimateHistory, Participant, estimate_pipeline, text_tokens
from .lineup import Lineup, LineupBuilder
from .media_estimate import style_wanted, with_media_step, with_style_step
from .planner import PlannerResult, run_planner
from .triage import Question, triage

CUSTOM = "custom"
WORKFLOWS = ("discussion", "collab")  # 讨论模式 / 协同模式
MEDIA_KINDS = KINDS
Source = Literal["rule", "model", "default"]


class RoutingError(ValueError):
    """用户选择无效，或可用模型无法满足所选档位。"""


@dataclass(frozen=True)
class UserChoice:
    """tier：档位名（routing.yaml 的 plans）或 "custom"；为空时用 default_plan。
    workflow：discussion（讨论模式）或 collab（协同模式）。"""

    tier: str | None = None
    models: tuple[str, ...] = ()
    coordinator: str | None = None
    workflow: str = "discussion"
    # 媒体输出（仅讨论模式）：image / speech / video；media_tier：媒体模型的档位
    media: str | None = None
    media_tier: str | None = None

    def __post_init__(self) -> None:
        if self.media is not None and self.media not in MEDIA_KINDS:
            raise RoutingError(f"未知的输出类型 {self.media!r}，可选：{list(MEDIA_KINDS)}")
        if self.media is not None and self.workflow != "discussion":
            raise RoutingError(
                "协同模式不需要选择输出类型：拆分子任务时统筹会决定哪些子任务要生成图片、语音或视频"
            )
        if self.media_tier not in (None, "budget", "flagship"):
            raise RoutingError(f"未知的媒体档位 {self.media_tier!r}，可选：budget / flagship")
        if self.workflow not in WORKFLOWS:
            raise RoutingError(f"未知的模式 {self.workflow!r}，可选：{list(WORKFLOWS)}")
        if self.tier == CUSTOM and not self.models:
            raise RoutingError("自选模式至少需要勾选模型")
        if self.tier != CUSTOM and (self.models or self.coordinator):
            raise RoutingError("只有自选模式可以勾选模型或指定统筹")
        if len(set(self.models)) != len(self.models):
            raise RoutingError("勾选的模型不能重复")
        if self.coordinator and self.coordinator not in self.models:
            raise RoutingError("统筹必须是勾选的模型之一")

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "models": list(self.models),
            "coordinator": self.coordinator,
            "workflow": self.workflow,
            "media": self.media,
            "media_tier": self.media_tier,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UserChoice:
        if "mode" in d and "tier" not in d:
            raise RoutingError("这是旧版本创建的会话（按难度分配人数），无法继续执行")
        return cls(
            d.get("tier"),
            tuple(d.get("models") or ()),
            d.get("coordinator"),
            d.get("workflow") or "discussion",
            d.get("media"),
            d.get("media_tier"),
        )


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
    lineup: Lineup | None = None


@dataclass(frozen=True)
class RoutingDecision:
    seed: int
    choice: UserChoice
    assessment: Assessment
    plan: str  # 档位名或 "custom"
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
            workflow=self.choice.workflow,
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
            absent=self.lineup.absent,
            estimated_cost_usd=self.estimate.total_usd,
            needs_confirmation=self.needs_confirmation,
            options={
                k: (v.estimate.total_usd if v.estimate else None) for k, v in self.options.items()
            },
        )


@dataclass(frozen=True)
class RoutingRecord:
    """每题一条，供以后调整规则：答案长度判断、阵容、预估与实际花费、是否升级。"""

    seed: int
    question_chars: int
    attachments: tuple[str, ...]
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
    absent: tuple[str, ...]
    estimated_cost_usd: float
    needs_confirmation: bool
    options: dict[str, float | None]
    workflow: str = "discussion"
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
        """忽略旧版本记录中已不存在的字段（如 mode / preset），缺少的字段用默认值。"""
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in known}
        for key in ("attachments", "require_tags", "rules_matched", "members", "absent"):
            data[key] = tuple(data.get(key) or ())
        if data.get("difficulty_source") not in ("rule", "model", "default"):
            data["difficulty_source"] = "default"
        return cls(**data)


# --- 内部工具 --------------------------------------------------------------------


def _route_price(router: ChannelRouter, model: ModelSpec) -> Price:
    """该模型当前会首先使用的渠道的价格。"""
    usable = router.plan(model.id).usable
    return model.price_for(usable[0]) if usable else model.price


def answer_tokens(config: AppConfig, a: Assessment) -> int:
    """预估的答案长度（token）：规划员的估计（限制在范围内），否则按难度取默认值。"""
    params = config.routing.estimate
    difficulty: Difficulty = a.difficulty or config.routing.default_difficulty
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
    history: EstimateHistory | None = None,
) -> CostEstimate:
    params = config.routing.estimate

    def seat(model_id: str) -> Participant:
        spec = by_id[model_id]
        return Participant(model_id, spec.tier, _route_price(router, spec))

    caps = {
        step: int(p["max_tokens"])
        for step, p in config.roundtable.step_params.items()
        if p.get("max_tokens")
    }
    members = [seat(m) for m in lineup.members]
    coordinator = seat(lineup.coordinator) if lineup.coordinator else None
    question_tokens = text_tokens(question.text, params) + question.attachment_tokens
    estimate = estimate_pipeline(
        lineup.pipeline,
        members=members,
        coordinator=coordinator,
        question_tokens=question_tokens,
        answer_tokens=answer_tokens,
        revise_rounds=config.roundtable.revise_rounds,
        params=params,
        reviews_per_answer=config.roundtable.reviews_per_answer,
        history=history,
        caps=caps,
        extra_calls=_tool_rounds(config),
        extra_usd=_tool_costs(config),
    )
    if question.media:
        estimate = with_media_step(
            estimate,
            config=config,
            router=router,
            kind=question.media,
            tier=question.media_tier or config.roundtable.media.default_tier,
            members=members,
            coordinator=coordinator,
            by_id=by_id,
            question_tokens=question_tokens,
            prompt_tokens=answer_tokens,
        )
    if style_wanted(
        config,
        router,
        has_style_images="image" in question.attachments,
        media=question.media,
        collab="decompose" in lineup.pipeline,
        tier=question.media_tier,
    ):
        estimate = with_style_step(
            estimate,
            config=config,
            members=members,
            coordinator=coordinator,
            by_id=by_id,
            question_tokens=question_tokens,
        )
    return estimate


def _tool_rounds(config: AppConfig) -> dict[str, float]:
    """能用工具的步骤：每次工具调用多一次模型调用，按 estimate.tool_rounds 估计。"""
    tools = config.roundtable.tools
    if not tools.enabled:
        return {}
    rounds = config.routing.estimate.tool_rounds
    return {step: rounds for step, names in tools.by_step.items() if names}


def _tool_costs(config: AppConfig) -> dict[str, float]:
    """能搜索的步骤：每个座位按 estimate.searches / fetches 次数和第一家搜索服务的单价估计。"""
    tools = config.roundtable.tools
    provider = next((p for p in config.models.search_providers.values() if p.enabled), None)
    if not tools.enabled or provider is None:
        return {}
    est = config.routing.estimate
    per_seat = est.searches * provider.price.per_search + est.fetches * provider.price.per_fetch
    return {step: per_seat for step, names in tools.by_step.items() if "search" in names}


def estimate_lineup(
    lineup: Lineup,
    question: Question,
    assessment: Assessment,
    *,
    config: AppConfig,
    router: ChannelRouter,
    history: EstimateHistory | None = None,
) -> CostEstimate:
    """按实际阵容估算花费（公开给编排引擎使用）。"""
    available = {m.id: m for m in config.models.models}
    return _estimate(
        lineup,
        config=config,
        router=router,
        by_id=available,
        question=question,
        answer_tokens=answer_tokens(config, assessment),
        history=history,
    )


def option_lineup(
    plan_name: str,
    *,
    seed: int,
    config: AppConfig,
    router: ChannelRouter,
    recent_coordinators: Sequence[str] = (),
    workflow: str = "discussion",
) -> Lineup:
    """重建某个档位的阵容（与估价时使用同一个随机种子，结果一致）。"""
    builder = LineupBuilder(
        config,
        router.available_models(),
        random.Random(f"{seed}:{plan_name}"),
        recent_coordinators=recent_coordinators,
    )
    try:
        return builder.build(config.routing.plans[plan_name], workflow)
    except NotEnoughModels as exc:
        raise RoutingError(str(exc)) from None


async def _assess(
    question: Question,
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
        source="rule",
        task_type=rules.task_type,
        require_tags=rules.require_tags,
        rules_matched=rules.matched,
        reason=f"规则 {rules.difficulty_rule}" if rules.difficulty_rule else "",
    )
    if rules.difficulty is not None:
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
    recent_coordinators: Sequence[str],
    workflow: str = "discussion",
    history: EstimateHistory | None = None,
) -> dict[str, PlanOption]:
    """每个档位的阵容与预估花费，供界面对比和用户改选。每个档位用独立的随机数。"""
    by_id = {m.id: m for m in available}
    options = {}
    for name, plan in config.routing.plans.items():
        builder = LineupBuilder(
            config,
            available,
            random.Random(f"{seed}:{name}"),
            recent_coordinators=recent_coordinators,
        )
        try:
            lineup = builder.build(plan, workflow)
        except NotEnoughModels as exc:
            options[name] = PlanOption(plan.label, False, None, str(exc))
            continue
        estimate = _estimate(
            lineup,
            config=config,
            router=router,
            by_id=by_id,
            question=question,
            answer_tokens=answer_tokens(config, assessment),
            history=history,
        )
        options[name] = PlanOption(plan.label, True, estimate, lineup=lineup)
    return options


# --- 提交前预估（不调用任何模型） ------------------------------------------------------


def rules_assessment(question: Question, config: AppConfig) -> Assessment:
    """只用规则判断答案长度（不调用规划员）；规则判断不出时用默认难度。"""
    rules = triage(question, config.routing.triage)
    if rules.difficulty is not None:
        return Assessment(
            difficulty=rules.difficulty,
            source="rule",
            task_type=rules.task_type,
            require_tags=rules.require_tags,
            rules_matched=rules.matched,
            reason=f"规则 {rules.difficulty_rule}",
        )
    return Assessment(
        difficulty=config.routing.default_difficulty,
        source="default",
        task_type=rules.task_type,
        require_tags=rules.require_tags,
        rules_matched=rules.matched,
        reason="提交前预估不调用规划员，按默认难度估计答案长度",
    )


@dataclass(frozen=True)
class PreviewOption:
    """提交前预估中的一项：某个模式 × 某个档位（或自选）。"""

    workflow: str
    plan: str
    label: str
    available: bool
    reason: str = ""
    estimate: CostEstimate | None = None
    lineup: Lineup | None = None


def preview_estimates(
    question: Question,
    *,
    config: AppConfig,
    router: ChannelRouter,
    seed: int,
    recent_coordinators: Sequence[str] = (),
    history: EstimateHistory | None = None,
    workflows: Sequence[str] = WORKFLOWS,
    custom: UserChoice | None = None,
) -> tuple[Assessment, list[PreviewOption]]:
    """提交前对比：各模式 × 各档位（及自选）的阵容与预计花费。不调用规划员，不产生费用。

    阵容按与正式路由相同的随机种子组建；提交时带上同一个 seed，上桌名单就与这里一致。
    """
    assessment = rules_assessment(question, config)
    available = [m for m in router.available_models() if m.seat]
    by_id = {m.id: m for m in available}
    tokens = answer_tokens(config, assessment)
    out: list[PreviewOption] = []
    for workflow in workflows:
        options = _options(
            question,
            assessment,
            config=config,
            router=router,
            available=available,
            seed=seed,
            recent_coordinators=recent_coordinators,
            workflow=workflow,
            history=history,
        )
        for name, option in options.items():
            out.append(
                PreviewOption(
                    workflow,
                    name,
                    option.label,
                    option.available,
                    option.reason,
                    option.estimate,
                    option.lineup,
                )
            )
        if custom is not None and custom.tier == CUSTOM:
            builder = LineupBuilder(
                config,
                available,
                random.Random(f"{seed}:{CUSTOM}"),
                recent_coordinators=recent_coordinators,
            )
            try:
                lineup = builder.build_custom(custom.models, custom.coordinator, workflow)
            except (NotEnoughModels, KeyError) as exc:
                out.append(
                    PreviewOption(workflow, CUSTOM, config.routing.custom.label, False, str(exc))
                )
                continue
            estimate = _estimate(
                lineup,
                config=config,
                router=router,
                by_id=by_id,
                question=question,
                answer_tokens=tokens,
                history=history,
            )
            out.append(
                PreviewOption(
                    workflow, CUSTOM, config.routing.custom.label, True, "", estimate, lineup
                )
            )
    return assessment, out


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
    history: EstimateHistory | None = None,
) -> RoutingDecision:
    choice = choice or UserChoice()
    routing = config.routing
    tier = choice.tier or routing.default_plan
    available = [m for m in router.available_models() if m.seat]
    by_id = {m.id: m for m in available}

    if tier != CUSTOM and tier not in routing.plans:
        raise RoutingError(f"未知的档位 {tier!r}，可选：{[*routing.plans, CUSTOM]}")
    if tier == CUSTOM:
        unknown = [m for m in choice.models if m not in by_id]
        if unknown:
            raise RoutingError(f"以下模型不可用（未启用或没有可用渠道）：{unknown}")

    assessment = await _assess(
        question,
        config=config,
        router=router,
        prompts=prompts,
        available=available,
        rng=random.Random(seed),
    )
    options = _options(
        question,
        assessment,
        config=config,
        router=router,
        available=available,
        seed=seed,
        recent_coordinators=recent_coordinators,
        workflow=choice.workflow,
        history=history,
    )
    if tier == CUSTOM:
        builder = LineupBuilder(
            config,
            available,
            random.Random(f"{seed}:{CUSTOM}"),
            recent_coordinators=recent_coordinators,
        )
        try:
            lineup = builder.build_custom(choice.models, choice.coordinator, choice.workflow)
        except NotEnoughModels as exc:
            raise RoutingError(str(exc)) from None
        estimate = _estimate(
            lineup,
            config=config,
            router=router,
            by_id=by_id,
            question=question,
            answer_tokens=answer_tokens(config, assessment),
            history=history,
        )
        options[CUSTOM] = PlanOption(routing.custom.label, True, estimate, lineup=lineup)
        escalate_to = None
    else:
        chosen = options[tier]
        if not chosen.available or chosen.lineup is None or chosen.estimate is None:
            raise RoutingError(chosen.reason)
        lineup, estimate = chosen.lineup, chosen.estimate
        escalate_to = routing.plans[tier].escalate_to
    return RoutingDecision(
        seed=seed,
        choice=choice,
        assessment=assessment,
        plan=tier,
        lineup=lineup,
        estimate=estimate,
        options=options,
        escalate_to=escalate_to,
        confirm_threshold_usd=routing.confirm_threshold_usd,
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
    history: EstimateHistory | None = None,
) -> RoutingDecision | None:
    """满足升级条件时，返回升级后的新决定（重新组建阵容、重新预估）；否则返回 None。

    升级不会自动执行：编排引擎总是先弹卡片询问用户。
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
        history=history,
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
    history: EstimateHistory | None = None,
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
        recent_coordinators=recent_coordinators,
    )
    try:
        lineup = builder.build(plan, choice.workflow)
    except NotEnoughModels as exc:
        raise RoutingError(f"无法升级到 {escalate_to}：{exc}") from None
    estimate = estimate_lineup(
        lineup, question, assessment, config=config, router=router, history=history
    )
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
