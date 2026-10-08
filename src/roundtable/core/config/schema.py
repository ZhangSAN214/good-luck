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
    total_usd: float = Field(gt=0)
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


# --- 整体 ----------------------------------------------------------------------


class AppConfig(_Strict):
    models: ModelsConfig
    roundtable: RoundtableConfig
    personas: PersonasConfig

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
        return self
