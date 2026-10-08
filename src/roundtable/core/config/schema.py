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
    # 参数改名（如 OpenAI 新模型要求 max_completion_tokens 而不是 max_tokens）
    param_aliases: dict[str, str] = Field(default_factory=dict)

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
    # False：只作为工具使用（如图像生成模型），不上桌、不需要档位
    seat: bool = True

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


class ReviewQuality(_Strict):
    min_checked_chars: int = Field(default=15, ge=0)
    vague_phrases: list[str] = Field(default_factory=list)


class EffortCheck(_Strict):
    """防偷懒：成员产出的实质内容检查（作答、互评、修订）。不合格打回重做一次，仍不合格标记敷衍。"""

    enabled: bool = True
    redo: bool = True  # 不合格时打回重做一次
    # 字数下限 = max(min_chars, 预估答案 token 数 × chars_per_expected_token)
    min_chars: int = Field(default=15, ge=0)
    chars_per_expected_token: float = Field(default=0.1, ge=0)
    # 去掉这些短语和标点后什么都不剩：空话（如"略""同上"）
    empty_phrases: list[str] = Field(default_factory=list)
    # 含有这些短语且全文不超过 refusal_max_chars：拒答
    refusal_phrases: list[str] = Field(default_factory=list)
    refusal_max_chars: int = Field(default=200, ge=0)
    # 与题目的相似度不低于此值：只复述题目
    restate_similarity: float = Field(default=0.8, gt=0, le=1)
    # 修订稿与另一位组员的答案相似度不低于此值（且两者都不短于 duplicate_min_chars）：疑似照抄
    duplicate_similarity: float = Field(default=0.95, gt=0, le=1)
    duplicate_min_chars: int = Field(default=100, ge=0)


class CollabRules(_Strict):
    """协同模式：拆分子任务的数量范围。"""

    max_subtasks: int = Field(default=12, ge=1)


class UploadRules(_Strict):
    """文件上传（附件）的限制。类型同时按扩展名和文件头判断。"""

    max_files: int = Field(default=5, ge=0)
    max_file_mb: float = Field(default=20, gt=0)
    max_pdf_pages: int = Field(default=50, ge=1)
    # 每个附件提取出的文字最多保留多少字（超出部分截断并注明）
    max_text_chars: int = Field(default=60000, ge=1000)
    # 扫描件判断：PDF 平均每页少于这么多字时提示"可能是扫描件"
    scanned_chars_per_page: int = Field(default=20, ge=0)
    # 预处理（图片文字版、音频转写）使用的输出上限
    describe_max_tokens: int = Field(default=3000, gt=0)
    transcribe_max_tokens: int = Field(default=8000, gt=0)
    # 上传文件的存放目录（相对项目根目录），文件按内容哈希命名
    storage_dir: str = "data/uploads"


ToolName = Literal["python", "write_file", "generate_image"]


class PythonTool(_Strict):
    """代码运行：backend 为 wasm（Deno + Pyodide）/ docker / auto（先 wasm 后 docker）/ off。"""

    backend: Literal["auto", "wasm", "docker", "off"] = "auto"
    timeout_s: float = Field(default=60, gt=0)  # 含加载 numpy 等包的时间
    memory_mb: int = Field(default=1024, ge=64)
    max_runs: int = Field(default=3, ge=1)  # 每位成员每个步骤最多运行几次（出错重跑也算）
    max_output_chars: int = Field(default=8000, ge=200)
    # wasm 后端的运行时目录（Deno 与 Pyodide）；为空时用 ROUNDTABLE_SANDBOX_DIR 或用户缓存目录
    runtime_dir: str | None = None
    docker_image: str = "roundtable-sandbox:1"
    # wasm 后端随附的包（由 scripts/setup_sandbox.py 下载）；pure 为从 PyPI 下载的纯 Python 包
    packages: list[str] = Field(
        default_factory=lambda: [
            "numpy",
            "pandas",
            "matplotlib",
            "sympy",
            "scipy",
            "pillow",
            "networkx",
            "lxml",
        ]
    )
    pure_packages: list[str] = Field(
        default_factory=lambda: [
            "openpyxl",
            "python-docx",
            "XlsxWriter",
            "fpdf2",
            "python-pptx",
            "seaborn",
        ]
    )


class FileRules(_Strict):
    """成员生成的文件：扩展名白名单、大小上限（单个、每场合计）。"""

    max_file_mb: float = Field(default=10, gt=0)
    max_session_mb: float = Field(default=100, gt=0)
    max_files_per_step: int = Field(default=20, ge=1)
    extensions: list[str] = Field(
        default_factory=lambda: [
            "py", "txt", "md", "csv", "json", "tex", "html", "svg",
            "xlsx", "docx", "pptx", "pdf", "png", "jpg", "jpeg", "gif", "webp",
        ]
    )  # fmt: skip
    # 转交给其他成员时，每个文本文件最多附上多少字
    share_text_chars: int = Field(default=3000, ge=0)


class ImageTool(_Strict):
    """图像生成：由带 image_gen 标签、seat: false 的模型完成（最便宜者优先）。"""

    max_per_step: int = Field(default=2, ge=0)
    max_tokens: int = Field(default=4000, gt=0)


class ToolsConfig(_Strict):
    enabled: bool = True
    max_rounds: int = Field(default=6, ge=1)  # 每次作答最多几轮工具调用
    # 步骤 → 可用的工具；同一步骤所有成员相同
    by_step: dict[str, list[ToolName]] = Field(default_factory=dict)
    python: PythonTool = PythonTool()
    files: FileRules = FileRules()
    image: ImageTool = ImageTool()


class RoundtableConfig(_Strict):
    seats: int = Field(ge=2)
    min_members: int = Field(default=2, ge=2)
    # 每份答案由几位其他组员评审（组员较少时等于全员互评）
    reviews_per_answer: int = Field(default=3, ge=1)
    budget: Budget
    token_threshold: int = Field(gt=0)
    coordinator: CoordinatorRule = CoordinatorRule()
    revise_rounds: int = Field(default=1, ge=1)
    prompts: dict[str, str]
    pipeline: list[str] = Field(min_length=1)  # 讨论模式
    collab_pipeline: list[str] = Field(min_length=1)  # 协同模式
    collab: CollabRules = CollabRules()
    channel_mode: ChannelMode = "auto"
    request: RequestPolicy = RequestPolicy()
    step_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    review_quality: ReviewQuality = ReviewQuality()
    effort_check: EffortCheck = EffortCheck()
    uploads: UploadRules = UploadRules()
    tools: ToolsConfig = ToolsConfig()

    @field_validator("prompts")
    @classmethod
    def _versions(cls, prompts: dict[str, str]) -> dict[str, str]:
        bad = {k: v for k, v in prompts.items() if not _PROMPT_VERSION.match(v)}
        if bad:
            raise ValueError(f"提示词版本必须形如 v1、v2：{bad}")
        return prompts

    @field_validator("pipeline", "collab_pipeline")
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


class PlanSpec(_Strict):
    """成员档位：tiers 中所有可用模型都上桌（一个当统筹，其余为组员）。"""

    label: str
    tiers: list[Tier] = Field(min_length=1)
    # None 表示使用 roundtable.yaml 的完整 pipeline
    pipeline: list[str] | None = None
    # 汇总仍有分歧或把握低时，询问是否用该档位重做
    escalate_to: str | None = None
    # 步骤 → 提示词角色（默认与步骤同名）
    prompt_roles: dict[str, str] = Field(default_factory=dict)

    _tiers = field_validator("tiers")(_unique_tiers)


class CustomSpec(_Strict):
    """自选：用户勾选上桌的模型。"""

    label: str = "自选"


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
    # 协同模式
    decompose_tokens: int = Field(default=800, ge=0)
    volunteer_tokens: int = Field(default=300, ge=0)
    assign_tokens: int = Field(default=400, ge=0)
    merge_tokens: int = Field(default=3000, ge=0)
    # 没有历史记录时，输出 token 按档位放大（推理模型的思考 token 也按输出计费）
    output_multiplier: dict[str, float] = Field(default_factory=dict)
    # 某个模型在某个步骤至少有这么多次成功调用，才用它的历史中位数代替公式
    history_min_samples: int = Field(default=3, ge=1)
    # 统计历史时只看最近这么多场讨论
    history_sessions: int = Field(default=30, ge=1)
    # 能用工具的步骤，每个座位平均多几次模型调用（工具轮次）；有历史记录后改用实际次数
    tool_rounds: float = Field(default=0.5, ge=0)
    # 每张图片按多少输入 token 估算（发原图的成员）
    image_tokens: int = Field(default=1500, ge=0)
    # 本桌实际花费超过"预估 × 此倍数"时暂停询问；None 关闭
    overrun_factor: float | None = Field(default=1.5, gt=1)

    @field_validator("answer_tokens")
    @classmethod
    def _all_difficulties(cls, v: dict[str, int]) -> dict[str, int]:
        missing = sorted(set(DIFFICULTIES) - set(v))
        if missing:
            raise ValueError(f"answer_tokens 缺少难度 {missing}")
        return v


class RoutingConfig(_Strict):
    confirm_threshold_usd: float = Field(default=0.30, ge=0)
    default_plan: str
    default_difficulty: Difficulty = "medium"
    planner: PlannerSpec = PlannerSpec()
    triage: list[TriageRule] = Field(default_factory=list)
    plans: dict[str, PlanSpec] = Field(min_length=1)
    custom: CustomSpec = CustomSpec()
    escalation: EscalationRule = EscalationRule()
    estimate: EstimateParams

    @model_validator(mode="after")
    def _references(self) -> RoutingConfig:
        if self.default_plan not in self.plans:
            raise ValueError(f"default_plan 引用了不存在的档位 {self.default_plan!r}")
        if "custom" in self.plans:
            raise ValueError("custom 是自选的保留名，不能用作档位名")
        for name, plan in self.plans.items():
            if plan.escalate_to is not None and plan.escalate_to not in self.plans:
                raise ValueError(
                    f"plans.{name}.escalate_to 引用了不存在的档位 {plan.escalate_to!r}"
                )
        for name in self.plans:  # 升级链不能成环
            seen, current = set(), name
            while current is not None:
                if current in seen:
                    raise ValueError(f"档位升级链成环：{name}")
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
        untiered = sorted(m.id for m in self.models.enabled if m.seat and m.tier is None)
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
            missing = sorted(set(plan.prompt_roles.values()) - set(rt.prompts))
            if missing:
                raise ValueError(
                    f"档位 {name} 的 prompt_roles 引用了 prompts 中没有的角色 {missing}"
                )
