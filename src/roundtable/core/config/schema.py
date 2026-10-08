"""配置文件的 pydantic schema。只描述与校验数据，不读文件、不读环境变量。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ChannelKind = Literal["aggregator", "direct", "local"]
ChannelMode = Literal["openrouter", "direct", "auto"]
# 档位：flagship 旗舰（高价高能力）/ budget 便宜档。成本优先路由只看档位和能力标签
Tier = Literal["flagship", "budget"]

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROMPT_VERSION = re.compile(r"^v\d+$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, protected_namespaces=())


# --- models.yaml ---------------------------------------------------------------


class Price(_Strict):
    """美元 / 每百万 token。cached_input 缺省时按 input 计（保守）。"""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: float | None = Field(default=None, ge=0)


class ChannelSpec(_Strict):
    """一个调用渠道（如 openrouter、anthropic 直连）。"""

    adapter: str
    kind: ChannelKind
    base_url: str
    # 读取 key 的环境变量名；None 表示该渠道不需要 key（如本地模型）
    key_env: str | None = None
    # 附加到每个请求体的字段（不得包含密钥）
    extra_body: dict[str, Any] = Field(default_factory=dict)

    @field_validator("key_env")
    @classmethod
    def _env_name(cls, v: str | None) -> str | None:
        if v is not None and not _ENV_NAME.match(v):
            raise ValueError(f"key_env 必须是大写环境变量名，收到 {v!r}")
        return v


class Route(_Strict):
    """模型在某个渠道上的调用方式。"""

    channel: str
    model: str = Field(min_length=1)
    # 该渠道的价格与默认价格不同时填写
    price: Price | None = None
    # 该渠道需要的额外/不同参数（如 max_completion_tokens），覆盖模型级 params
    params: dict[str, Any] = Field(default_factory=dict)


class ModelSpec(_Strict):
    id: str = Field(min_length=1)
    vendor: str = Field(min_length=1)
    tier: Tier | None = None
    price: Price
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    # 产品名、中文名等别称；转发给其他模型前会被遮蔽（见 allocation.identity）
    aliases: list[str] = Field(default_factory=list)
    # 按优先顺序排列的渠道
    routes: list[Route] = Field(min_length=1)

    @field_validator("routes")
    @classmethod
    def _unique_channels(cls, routes: list[Route]) -> list[Route]:
        channels = [r.channel for r in routes]
        dupes = sorted({c for c in channels if channels.count(c) > 1})
        if dupes:
            raise ValueError(f"同一模型的渠道不能重复：{dupes}")
        return routes

    def price_for(self, route: Route) -> Price:
        return route.price or self.price

    def params_for(self, route: Route) -> dict[str, Any]:
        return {**self.params, **route.params}


class ModelsConfig(_Strict):
    tag_vocabulary: list[str]
    channels: dict[str, ChannelSpec]
    models: list[ModelSpec]

    @model_validator(mode="after")
    def _cross_check(self) -> ModelsConfig:
        ids = [m.id for m in self.models]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"模型 id 重复：{dupes}")
        vocab = set(self.tag_vocabulary)
        for m in self.models:
            unknown = sorted(set(m.tags) - vocab)
            if unknown:
                raise ValueError(f"模型 {m.id} 含未知标签 {unknown}（见 tag_vocabulary）")
            for r in m.routes:
                if r.channel not in self.channels:
                    raise ValueError(f"模型 {m.id} 引用了未定义的渠道 {r.channel!r}")
        return self

    def get(self, model_id: str) -> ModelSpec:
        for m in self.models:
            if m.id == model_id:
                return m
        raise KeyError(model_id)

    @property
    def enabled(self) -> list[ModelSpec]:
        return [m for m in self.models if m.enabled]


# --- roundtable.yaml -----------------------------------------------------------


class Budget(_Strict):
    """按 UTC 自然月 / 自然日统计花费；每月 1 号、每天 0 点（UTC）自动进入新周期。"""

    monthly_usd: float = Field(gt=0)
    # None 表示不设每日上限
    daily_usd: float | None = Field(default=None, gt=0)
    warn_ratio: float = Field(gt=0, lt=1)


class CoordinatorRule(_Strict):
    strategy: Literal["rotate", "random", "fixed"] = "rotate"
    fixed_id: str | None = None

    @model_validator(mode="after")
    def _fixed_needs_id(self) -> CoordinatorRule:
        if self.strategy == "fixed" and not self.fixed_id:
            raise ValueError("coordinator.strategy 为 fixed 时必须填写 fixed_id")
        return self


class RequestPolicy(_Strict):
    timeout_s: float = Field(default=120, gt=0)
    # 全部渠道都失败后，从头再试的轮数（含第一轮）
    failover_rounds: int = Field(default=2, ge=1)
    # 每轮之间的等待秒数
    backoff_s: float = Field(default=2.0, ge=0)
    # 渠道出错后的冷却时间：冷却中的渠道被排到最后
    cooldown_rate_limit_s: float = Field(default=30, ge=0)
    cooldown_quota_s: float = Field(default=600, ge=0)


class RoundtableConfig(_Strict):
    seats: int = Field(ge=2)
    min_members: int = Field(default=2, ge=2)
    budget: Budget
    token_threshold: int = Field(gt=0)
    coordinator: CoordinatorRule = CoordinatorRule()
    revise_rounds: int = Field(default=1, ge=1)
    prompts: dict[str, str]
    pipeline: list[str] = Field(min_length=1)
    channel_mode: ChannelMode = "auto"
    request: RequestPolicy = RequestPolicy()

    @field_validator("prompts")
    @classmethod
    def _versions(cls, prompts: dict[str, str]) -> dict[str, str]:
        bad = {k: v for k, v in prompts.items() if not _PROMPT_VERSION.match(v)}
        if bad:
            raise ValueError(f"提示词版本必须形如 v1、v2：{bad}")
        return prompts

    @field_validator("pipeline")
    @classmethod
    def _unique_steps(cls, steps: list[str]) -> list[str]:
        if len(steps) != len(set(steps)):
            raise ValueError(f"pipeline 中的步骤不能重复：{steps}")
        return steps

    @model_validator(mode="after")
    def _members(self) -> RoundtableConfig:
        if self.min_members > self.seats:
            raise ValueError("min_members 不能大于 seats")
        return self


# --- personas.yaml -------------------------------------------------------------


class Persona(_Strict):
    """人设模式（后续扩展）使用；v1 不读取。"""

    nickname: str | None = None
    personality: str | None = None
    catchphrase: str | None = None
    specialties: list[str] = Field(default_factory=list)


class PersonasConfig(_Strict):
    code_prefix: str = "组员"
    codes: list[str] = Field(min_length=2)
    personas: dict[str, Persona] = Field(default_factory=dict)

    @field_validator("codes")
    @classmethod
    def _unique_codes(cls, codes: list[str]) -> list[str]:
        if len(codes) != len(set(codes)):
            raise ValueError("代号不能重复")
        return codes


# --- routing.yaml --------------------------------------------------------------

Difficulty = Literal["simple", "medium", "hard"]
DIFFICULTIES: tuple[Difficulty, ...] = ("simple", "medium", "hard")
Confidence = Literal["high", "medium", "low"]


def _unique_tiers(tiers: list[Tier]) -> list[Tier]:
    if len(tiers) != len(set(tiers)):
        raise ValueError(f"档位不能重复：{tiers}")
    return tiers


class MemberPick(_Strict):
    tiers: list[Tier] = Field(min_length=1)
    min: int = Field(default=1, ge=1)
    max: int = Field(default=1, ge=1)

    _tiers = field_validator("tiers")(_unique_tiers)

    @model_validator(mode="after")
    def _range(self) -> MemberPick:
        if self.min > self.max:
            raise ValueError("members.min 不能大于 members.max")
        return self


class CoordinatorPick(_Strict):
    tiers: list[Tier] = Field(min_length=1)

    _tiers = field_validator("tiers")(_unique_tiers)


class PlanSpec(_Strict):
    label: str
    members: MemberPick
    coordinator: CoordinatorPick | None = None
    # None 表示使用 roundtable.yaml 的完整 pipeline
    pipeline: list[str] | None = None
    escalate_to: str | None = None


class RuleCondition(_Strict):
    min_chars: int | None = Field(default=None, ge=0)
    max_chars: int | None = Field(default=None, ge=0)
    any_keywords: list[str] = Field(default_factory=list)
    no_keywords: list[str] = Field(default_factory=list)
    attachment_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _not_empty(self) -> RuleCondition:
        if not self.model_fields_set:
            raise ValueError("规则至少需要一个条件")
        return self


class RuleAction(_Strict):
    difficulty: Difficulty | None = None
    require_tags: list[str] = Field(default_factory=list)
    task_type: str | None = None

    @model_validator(mode="after")
    def _not_empty(self) -> RuleAction:
        if self.difficulty is None and not self.require_tags and self.task_type is None:
            raise ValueError("规则至少需要一个动作（difficulty / require_tags / task_type）")
        return self


class TriageRule(_Strict):
    name: str = Field(min_length=1)
    when: RuleCondition
    then: RuleAction


class PresetSpec(_Strict):
    label: str
    plan: str
    escalate: bool = False


class ManualSpec(_Strict):
    coordinator: CoordinatorPick


class PlannerSpec(_Strict):
    tiers: list[Tier] = Field(default_factory=lambda: ["budget"], min_length=1)
    max_tokens: int = Field(default=300, gt=0)
    expected_input_tokens: int = Field(default=700, gt=0)
    expected_output_tokens: int = Field(default=150, gt=0)


class EscalationRule(_Strict):
    min_disagreements: int = Field(default=1, ge=1)
    confidence: list[Confidence] = Field(default_factory=lambda: ["low"])


class TokenBounds(_Strict):
    min: int = Field(gt=0)
    max: int = Field(gt=0)


class EstimateParams(_Strict):
    cjk_chars_per_token: float = Field(default=1.0, gt=0)
    other_chars_per_token: float = Field(default=4.0, gt=0)
    prompt_overhead_tokens: int = Field(default=500, ge=0)
    answer_tokens: dict[Difficulty, int]
    answer_tokens_bounds: TokenBounds
    review_tokens_per_peer: int = Field(default=400, ge=0)
    revise_overhead_tokens: int = Field(default=300, ge=0)
    synthesize_tokens: int = Field(default=1500, ge=0)

    @field_validator("answer_tokens")
    @classmethod
    def _all_difficulties(cls, v: dict[str, int]) -> dict[str, int]:
        missing = sorted(set(DIFFICULTIES) - set(v))
        if missing:
            raise ValueError(f"answer_tokens 缺少难度 {missing}")
        return v


class RoutingConfig(_Strict):
    confirm_threshold_usd: float = Field(default=0.30, ge=0)
    default_difficulty: Difficulty = "medium"
    prefer_distinct_vendors: bool = True
    prefer_task_tags: bool = True
    planner: PlannerSpec = PlannerSpec()
    triage: list[TriageRule] = Field(default_factory=list)
    plans: dict[str, PlanSpec]
    difficulty_plans: dict[Difficulty, str]
    presets: dict[str, PresetSpec]
    manual: ManualSpec
    escalation: EscalationRule = EscalationRule()
    estimate: EstimateParams

    @model_validator(mode="after")
    def _references(self) -> RoutingConfig:
        missing = sorted(set(DIFFICULTIES) - set(self.difficulty_plans))
        if missing:
            raise ValueError(f"difficulty_plans 缺少难度 {missing}")
        for where, plan in [
            *(("difficulty_plans." + d, p) for d, p in self.difficulty_plans.items()),
            *(("presets." + n, p.plan) for n, p in self.presets.items()),
            *(("plans." + n + ".escalate_to", p.escalate_to) for n, p in self.plans.items()),
        ]:
            if plan is not None and plan not in self.plans:
                raise ValueError(f"{where} 引用了不存在的方案 {plan!r}")
        for name in self.plans:  # 升级链不能成环
            seen, current = set(), name
            while current is not None:
                if current in seen:
                    raise ValueError(f"方案升级链成环：{name}")
                seen.add(current)
                current = self.plans[current].escalate_to
        names = [r.name for r in self.triage]
        if len(names) != len(set(names)):
            raise ValueError("triage 规则名不能重复")
        return self


# --- 整体 ----------------------------------------------------------------------


class AppConfig(_Strict):
    models: ModelsConfig
    roundtable: RoundtableConfig
    personas: PersonasConfig
    routing: RoutingConfig

    @model_validator(mode="after")
    def _cross_check(self) -> AppConfig:
        rt, personas = self.roundtable, self.personas
        if len(personas.codes) < rt.seats:
            raise ValueError(f"代号池只有 {len(personas.codes)} 个，少于座位数 {rt.seats}")
        ids = {m.id for m in self.models.models}
        if rt.coordinator.fixed_id and rt.coordinator.fixed_id not in ids:
            raise ValueError(f"coordinator.fixed_id {rt.coordinator.fixed_id!r} 不在模型列表中")
        unknown = sorted(set(personas.personas) - ids)
        if unknown:
            raise ValueError(f"personas 中有未知模型 id：{unknown}")
        self._check_routing()
        return self

    def _check_routing(self) -> None:
        rt, routing = self.roundtable, self.routing
        untiered = sorted(m.id for m in self.models.enabled if m.tier is None)
        if untiered:
            raise ValueError(f"成本优先路由只按档位分配，以下启用的模型缺少 tier：{untiered}")
        vocab = set(self.models.tag_vocabulary)
        for rule in routing.triage:
            unknown_tags = sorted(set(rule.then.require_tags) - vocab)
            if unknown_tags:
                raise ValueError(f"triage 规则 {rule.name} 引用了未知标签 {unknown_tags}")
            if rule.then.task_type and rule.then.task_type not in vocab:
                raise ValueError(f"triage 规则 {rule.name} 的 task_type 不在标签词表中")
        if "planner" not in rt.prompts:
            raise ValueError("roundtable.yaml 的 prompts 缺少 planner 版本")
        for name, plan in routing.plans.items():
            if plan.members.max > rt.seats:
                raise ValueError(f"方案 {name} 的 members.max 超过座位数 {rt.seats}")
            steps = plan.pipeline if plan.pipeline is not None else rt.pipeline
            if ("synthesize" in steps) != (plan.coordinator is not None):
                raise ValueError(f"方案 {name}：有 synthesize 步骤时必须配置 coordinator，反之亦然")
            if "review" in steps and plan.members.min < 2:
                raise ValueError(f"方案 {name}：含互评步骤时 members.min 至少为 2")
