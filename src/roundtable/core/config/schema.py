"""配置文件的 pydantic schema。只描述与校验数据，不读文件、不读环境变量。"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from roundtable.core.config.sections.base import (
    Tier,
    _Strict,
)
from roundtable.core.config.sections.chat import (
    BrowseRules,
    ChatActionRules,
    ChatBudgetRules,
    ChatRules,
    ClassicMeme,
    DiaryToolRules,
    EmojiImageRules,
    FeedSpec,
    FestivalRules,
    FestivalSpec,
    LifeGuardRules,
    LifeRules,
    LifeWayRules,
    LowRules,
    MemeRules,
    MemeSource,
    MemoryLinkRules,
    MemoryRules,
    MomentForm,
    MomentsRules,
    PerceptionListen,
    PerceptionMode,
    PerceptionRules,
    PlainTalkCheck,
    PlainTalkRule,
    PlainTalkRules,
    ProgressKind,
    ProgressRules,
    StickerPackRules,
    StoryRules,
    StorySpec,
    StoryStep,
    TalentDomain,
    TalentRules,
    TalentTexts,
    TravelDest,
    TravelRules,
    TravelTexts,
    WanderRules,
    WanderSite,
    WatchRules,
    _evening_hours,
)
from roundtable.core.config.sections.member_engine import (
    AboutRules,
    AmbivalenceRules,
    AskedRules,
    AssocRules,
    AttachStyle,
    BaselineRules,
    BodyRhythm,
    BrainRules,
    BusRules,
    CadenceRules,
    CardRules,
    CareRules,
    CircadianRules,
    CircleRules,
    ConfabRules,
    ContextRules,
    DriftRules,
    DriveRule,
    EmpathyRules,
    EngineRules,
    EpisodeRules,
    EvalRules,
    FeelRules,
    FirstsRules,
    FlawRule,
    FloorRules,
    GistRule,
    GoalRules,
    GrowthBookRules,
    GrowthHabitRules,
    GrowthPlasticRules,
    GrowthReportRules,
    GrowthReviewRules,
    GrowthRules,
    GrowthVitalsRules,
    HeartbeatRules,
    HooksRules,
    HumanRules,
    HushRules,
    InsightDepth,
    InsightRules,
    IntentRule,
    LearnRules,
    LingoRules,
    LoveRules,
    LoveStyle,
    MetaRules,
    NickRules,
    OpenerRules,
    PaceRules,
    PhaseKind,
    PhaseRules,
    PhrasebookRules,
    PhraseEntry,
    PhraseLearnRules,
    PoleRule,
    Privilege,
    ProfileRules,
    PsycheRules,
    ReachRules,
    ReflexRule,
    RelationRules,
    RelationStage,
    RelationWeights,
    RewardRules,
    RhythmRules,
    RoutineRules,
    SchedulerRules,
    SchedulerShares,
    ShowRule,
    ShowRules,
    SlopRules,
    StashFollowRules,
    StashRules,
    StateLayerRules,
    StateRules,
    Strategy,
    SwayRules,
    TensionRule,
    TraitPick,
    TypoRules,
    ValueAxis,
    ValueRules,
    VoiceCheckRules,
    VoiceMode,
    VoiceRules,
    WhimRules,
)
from roundtable.core.config.sections.office import (
    ClipperRules,
    EvolutionImplementRules,
    EvolutionPluginRules,
    EvolutionRules,
    EvolutionSchedule,
    EvolutionScoutRules,
    OfficeMeeting,
    OfficePerformance,
    OfficeProject,
    OfficeRules,
    OfficeTaskCaps,
)
from roundtable.core.config.sections.terminal import (
    _DATE_RULE,
    BackupRules,
    CalendarObservance,
    CalendarRegion,
    CalendarRules,
    GameModeRules,
    LunarFestival,
    MailAIRules,
    MailConfig,
)

ChannelKind = Literal["aggregator", "direct", "local"]
ChannelMode = Literal["openrouter", "direct", "auto"]
ImageApi = Literal["chat", "images"]
OfficeRole = Literal["manager", "staff"]

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_PROMPT_VERSION = re.compile(r"^v\d+$")


# --- models.yaml ---------------------------------------------------------------


class Price(_Strict):
    """美元 / 每百万 token。cached_input 缺省时按 input 计（保守）。"""

    input: float = Field(ge=0)
    output: float = Field(ge=0)
    cached_input: float | None = Field(default=None, ge=0)


# 媒体模型的计价单位：张、秒、分钟、字符；按 token 计价的模型沿用 Price
MediaUnit = Literal["image", "second", "minute", "char", "song"]  # song：每生成一首 / 一段


class MediaPrice(_Strict):
    """媒体模型每个单位的价格（美元）：图片每张、视频每秒、转写每分钟、语音合成每字符、音乐每首。"""

    unit: MediaUnit
    usd: float = Field(ge=0)


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
    # 步骤参数里的中立参数 reasoning（思考强度 / 预算）怎么发给该渠道：
    # object = {"reasoning": {...}}（OpenRouter）；effort = {"reasoning_effort": "low"}；
    # toggle = {"enable_thinking": true/false}（少想 low / 不想 none 时关掉，medium / high 时开；
    # 给了思考 token 预算时另发 thinking_budget；如百炼兼容模式，默认开着思考）；
    # none = 不发送（渠道不支持时忽略，不报错）。不填：聚合平台 object，其余 none
    reasoning: Literal["object", "effort", "toggle", "none"] | None = None
    # 账户余额查询（只读，供「余额与用量」面板）：credits = GET {base_url}/credits（账户充值与已用）
    # 加 GET {base_url}/key（这个 key 的额度与今天 / 本周 / 本月已用）。不填表示该渠道不查余额
    balance: Literal["credits"] | None = None
    # 每月赠送 / 包含的额度（美元，如会员附送的 API 额度）。渠道优先顺序据此判断：
    # 本月按本机账本还没用完时排在前面，用完后排到最后。可在界面里改（存 app_settings）
    monthly_credit_usd: float | None = Field(default=None, ge=0)
    # 随消息发的音频（input_audio.data）写成 data URI（"data:;base64,…"）而不是纯 base64：
    # 有的 OpenAI 兼容渠道（如百炼的兼容模式）只认这种写法
    media_data_uri: bool = False
    # 这个渠道用本机显卡（本地出图、本地配音）：游戏模式下不调用（docs/GAMEMODE.md）
    gpu: bool = False
    # 每周花费上限（美元，UTC 周一 0 点起算，2026-11-04 用户定 DeepSeek 一周 ¥10）：
    # 本周按本机账本走这个渠道的花费，加上和它共用 key 的 dsh 工作台（dsh.cap_channel）花的，
    # 到了就不再走这个渠道，同一模型有别的渠道就换过去；dsh 也停到下周
    weekly_cap_usd: float | None = Field(default=None, ge=0)
    # 每月花费上限（美元，UTC 每月 1 号起算，2026-11-04 用户：会员每月送的 Claude API 额度
    # $100 不超）：本月按本机账本走这个渠道的花费到了就不再走它，同一模型有别的渠道就换过去
    monthly_cap_usd: float | None = Field(default=None, ge=0)
    # 提示缓存（anthropic 适配器）：请求带顶层 cache_control，重复的开头（人设、聊天记录）
    # 第二次起按缓存价计费；开头太短时接口自己不缓存，不报错
    prompt_cache: bool = False

    @property
    def reasoning_style(self) -> str:
        return self.reasoning or ("object" if self.kind == "aggregator" else "none")

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
    media_price: MediaPrice | None = None
    # 该渠道需要的额外/不同参数（如 max_completion_tokens），覆盖模型级 params
    params: dict[str, Any] = Field(default_factory=dict)
    # 画图模型在该渠道用哪种接口（覆盖模型级 image_api）
    image_api: ImageApi | None = None


class ModelSpec(_Strict):
    id: str = Field(min_length=1)
    vendor: str = Field(min_length=1)
    tier: Tier | None = None
    # 按 token 计价；只按张 / 秒 / 分钟 / 字符计价的媒体模型写 0 并填 media_price
    price: Price = Price(input=0.0, output=0.0)
    media_price: MediaPrice | None = None
    # 按 token 计价的图像模型：生成一张图的典型输出 token 数（price.output 此时是图像输出的价格）。
    # 只用于预估和渠道没有返回用量时的记账；渠道返回实际费用时一律以实际为准
    image_tokens: int | None = Field(default=None, ge=1)
    # 按 token 计价的语音合成模型：每个字符约对应的音频输出 token 数（预估、渠道没返回费用时记账）
    speech_tokens_per_char: float | None = Field(default=None, gt=0)
    # 媒体模型的默认项：同一候选组里有默认模型时，只在默认模型之间选（见 media/select.py）
    default: bool = False
    tags: list[str] = Field(default_factory=list)
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    # 画图模型调用的接口：chat = 对话接口（返回图片，如 Gemini 图像模型）；
    # images = 专门的图像接口（OpenRouter 的 /api/v1/images，OpenAI 的 gpt-image 系列只能走这个）
    image_api: ImageApi = "chat"
    # 画图模型吃哪种提示词：text = 自然语言（对话式 / 云端绘图模型）；tags = 逗号分隔的标签
    # （本地 SDXL 二次元底模）。朋友圈配图据此选提示词（moment_photo / moment_photo_tags）
    image_prompt: Literal["text", "tags"] = "text"
    # 产品名、中文名等别称；转发给其他模型前会被遮蔽（见 allocation.identity）
    aliases: list[str] = Field(default_factory=list)
    # 按优先顺序排列的渠道
    routes: list[Route] = Field(min_length=1)
    # False：只作为工具使用（如图像生成模型），不上桌、不需要档位
    seat: bool = True
    # 办公室模式的岗位：manager = 经理（收活、分诊、派活、拍板、写最终交付，由用户指定），
    # staff = 员工（默认）。只在办公室模式生效；讨论 / 协同等旧模式不看它
    office_role: OfficeRole = "staff"

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

    def media_price_for(self, route: Route) -> MediaPrice | None:
        return route.media_price or self.media_price

    def params_for(self, route: Route) -> dict[str, Any]:
        return {**self.params, **route.params}

    def image_api_for(self, route: Route) -> ImageApi:
        return route.image_api or self.image_api


class SearchPrice(_Strict):
    per_search: float = Field(default=0.0, ge=0)  # 美元 / 次搜索
    per_fetch: float = Field(default=0.0, ge=0)  # 美元 / 读取一个网页


class SearchProviderSpec(_Strict):
    """联网搜索服务（成员的 search / fetch 工具使用）。按 search_providers 的顺序优先使用。"""

    adapter: str
    base_url: str
    key_env: str | None = None
    price: SearchPrice = SearchPrice()
    params: dict[str, Any] = Field(default_factory=dict)  # 附加到每次搜索请求的参数
    enabled: bool = True

    @field_validator("key_env")
    @classmethod
    def _env_name(cls, v: str | None) -> str | None:
        return ChannelSpec._env_name(v)


class ModelsConfig(_Strict):
    tag_vocabulary: list[str]
    channels: dict[str, ChannelSpec]
    models: list[ModelSpec]
    search_providers: dict[str, SearchProviderSpec] = Field(default_factory=dict)
    # 模型开关：模型 id → 是否启用，覆盖条目里的 enabled。写在 config/models.local.yaml 里
    # 就能在本机打开 / 关掉某个模型，不用把整张 models 列表抄过去（列表合并时整体替换）
    model_switches: dict[str, bool] = Field(default_factory=dict)
    # 模型参数：模型 id → 要覆盖的 params（逐项合并进条目的 params）。同样写在 models.local.yaml，
    # 用来按本机实际情况改某个模型的参数（如本地配音服务的接口路径和字段）
    model_params: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _apply_switches(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        switches = data.get("model_switches") or {}
        extra = data.get("model_params") or {}
        if not isinstance(switches, dict) or not isinstance(extra, dict):
            return data  # 交给字段校验报错
        if not switches and not extra:
            return data
        models = data.get("models") or []
        known = {m.get("id") for m in models if isinstance(m, dict)}
        for field, given in (("model_switches", switches), ("model_params", extra)):
            unknown = sorted(str(k) for k in given if k not in known)
            if unknown:
                raise ValueError(f"{field} 里有不存在的模型：{unknown}")
        out = []
        for m in models:
            if isinstance(m, dict) and m.get("id") in switches:
                m = {**m, "enabled": switches[m["id"]]}
            if isinstance(m, dict) and isinstance(extra.get(m.get("id")), dict):
                m = {**m, "params": {**(m.get("params") or {}), **extra[m["id"]]}}
            out.append(m)
        return {**data, "models": out}

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
            if m.office_role == "manager" and not m.seat:
                raise ValueError(f"模型 {m.id} 是经理，必须能上桌（seat: true）")
        return self

    @property
    def managers(self) -> list[ModelSpec]:
        """办公室模式的经理（启用的、office_role 为 manager 的模型，按配置顺序）。"""
        return [m for m in self.enabled if m.office_role == "manager"]

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


class ChannelPriority(_Strict):
    """同一模型有多个渠道时的先后（只决定走哪条路，不影响谁上桌）。

    auto：按 models.yaml 里各模型 routes 的顺序；custom：按 order 列出的渠道先后，没列出的排在后面。
    两种方式下，填了每月额度的渠道本月还有余额时排最前，用完后排最后；冷却中的渠道始终排最后。
    界面里的修改存在 app_settings，优先于这里。
    """

    strategy: Literal["auto", "custom"] = "auto"
    order: list[str] = Field(default_factory=list)


class RequestPolicy(_Strict):
    timeout_s: float = Field(default=120, gt=0)
    # 全部渠道都失败后，从头再试的轮数（含第一轮）
    failover_rounds: int = Field(default=2, ge=1)
    # 每轮之间的等待秒数
    backoff_s: float = Field(default=2.0, ge=0)
    # 渠道出错后的冷却时间：冷却中的渠道被排到最后
    cooldown_rate_limit_s: float = Field(default=30, ge=0)
    cooldown_quota_s: float = Field(default=600, ge=0)
    # 限速与并发（每个渠道分别计）：同时在途的请求数上限（全员并发调用时避免触发限流）；
    # 每分钟请求数上限，None 表示不限。超过时排队等待，不报错
    max_concurrent: int = Field(default=6, ge=1)
    requests_per_minute: int | None = Field(default=None, ge=1)


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


class KindRule(_Strict):
    """子任务类型：流水线里的一种工作。规则全在配置里，代码不认具体的类型名。"""

    label: str
    meaning: str = ""  # 一句话说明这类工作做什么（写进给统筹的类型词表）
    prompt: str  # 负责人完成这类子任务用的提示词角色（roundtable.yaml 的 prompts 里要有）
    tags: list[str] = Field(default_factory=list)  # 偏好的能力标签（分配时加分）
    parallel: bool = True  # 能否按内容拆成多个平行子任务（成员比步骤多时用）
    needs: list[str] = Field(default_factory=list)  # 必须依赖其中某一类的子任务
    audit: bool = False  # 审查类：必须依赖至少一个非审查类的子任务
    min_upstream: int = Field(default=0, ge=0)  # 至少依赖几个子任务（整合类）
    requires_media: bool = False  # 需要可用的媒体生成模型
    avoid_hard: list[str] = Field(default_factory=list)  # 负责人不能是这些类型上游子任务的负责人
    avoid_soft: list[str] = Field(default_factory=list)  # 同上，但成员太少无法满足时放宽
    # 完成这类子任务时覆盖「work」步骤参数（如 max_tokens）：同一类型的所有成员相同，与成员是谁无关
    step_params: dict[str, Any] = Field(default_factory=dict)


class PipelineRules(_Strict):
    """流水线拆分的校验：不合格就让统筹重拆一次，仍不合格按模板自动生成。"""

    enabled: bool = True
    min_members: int = Field(default=2, ge=2)  # 成员数达到才启用
    min_kinds: int = Field(default=3, ge=1)  # 至少有几种不同类型（子任务更少时取子任务数）
    # 判定"整道题当一个子任务"：标题 / 要求含这些短语，或与题目相似度不低于阈值
    whole_task_phrases: list[str] = Field(default_factory=list)
    whole_task_similarity: float = Field(default=0.75, gt=0, le=1)
    # 同类型、同上游的平行子任务，内容相似度不低于阈值视为重复（几个人做同一份内容）
    sibling_similarity: float = Field(default=0.95, gt=0, le=1)


class TemplateDep(_Strict):
    id: str
    gives: str = ""


class TemplateStep(_Strict):
    id: str
    kind: str
    title: str
    requirements: str = ""
    acceptance: str = ""
    depends_on: list[TemplateDep | str] = Field(default_factory=list)
    media: str | None = None
    # 成员比步骤多时，这一步按内容拆成几个平行部分；split_by 写怎么分（会写进每个部分的要求）
    split: bool = False
    split_by: str = ""
    split_hints: list[str] = Field(default_factory=list)


class TemplateWhen(_Strict):
    """选用条件，全部满足才选用；什么都不写表示总是满足。"""

    media: list[str] = Field(default_factory=list)  # 这些媒体种类都有可用的模型
    image_attachment: bool | None = None  # 题目是否带图片附件
    tools: list[str] = Field(default_factory=list)  # 这些工具都可用
    keywords: list[str] = Field(default_factory=list)  # 题目含其中任一词（不区分大小写）


class PipelineTemplate(_Strict):
    name: str
    label: str
    when: TemplateWhen = TemplateWhen()
    steps: list[TemplateStep] = Field(min_length=2)


class DiscussionRules(_Strict):
    """分工讨论：分工后组员提建议，统筹最多调整一次。"""

    enabled: bool = True
    min_members: int = Field(default=3, ge=2)  # 组员不足时跳过（两个人没什么好商量的）
    max_suggestions: int = Field(default=3, ge=1)  # 每位组员最多提几条


class Lens(_Strict):
    """头脑风暴的一种发散手法：名称 + 一句做法提示。"""

    name: str = Field(min_length=1, max_length=20)
    hint: str = Field(min_length=1, max_length=200)


class BrainstormRules(_Strict):
    """头脑风暴：每人最多提几个想法、拓展几条，投票与结果页的条数，导图的分支与条目上限。"""

    max_ideas: int = Field(default=4, ge=1)
    max_expansions: int = Field(default=3, ge=0)
    crossbreed_sample: int = Field(default=4, ge=2)
    vote_picks: int = Field(default=5, ge=1)
    top: int = Field(default=5, ge=1)
    stories: int = Field(default=3, ge=0)
    # 发散手法：按随机种子分给组员（与成员是谁无关）；为空时不分手法
    lenses: list[Lens] = Field(default_factory=list)
    max_branches: int = Field(default=6, ge=1)
    max_ideas_per_branch: int = Field(default=6, ge=1)
    max_next_steps: int = Field(default=5, ge=0)


class AcceptanceRules(_Strict):
    """合并前的逐条验收：统筹按验收标准检查，不合格的退回作者改一次。"""

    enabled: bool = True
    max_fix_items: int = Field(default=3, ge=1)  # 最多退回几个子任务（防止成本失控）


class CrossReviewRules(_Strict):
    """交叉审查的花费控制：很短的成果少找几位审查者（与作者是谁无关）。"""

    short_chars: int = Field(default=0, ge=0)  # 成果少于这么多字算"很短"；0 = 不启用
    short_reviews: int = Field(default=1, ge=1)  # 很短的成果找几位审查者


class ClarifyRules(_Strict):
    """下游向上游提澄清问题：有上游产出的文字子任务，负责人动手前最多问一次（所有人同一规则）。"""

    enabled: bool = True
    max_question_chars: int = Field(default=300, ge=20)  # 问题超长时截断


class MergeTrimRules(_Strict):
    """合并时省钱：已经交给下游、且下游成果合格的上游成果只给统筹看开头摘录（只看依赖关系）。"""

    enabled: bool = True
    excerpt_chars: int = Field(default=600, ge=100)  # 摘录长度；不超过这么多字的成果仍给全文


class CollabRules(_Strict):
    """协同模式：拆分子任务的数量范围，以及流水线（子任务类型、校验、模板）。"""

    max_subtasks: int = Field(default=12, ge=1)
    discussion: DiscussionRules = DiscussionRules()
    acceptance: AcceptanceRules = AcceptanceRules()
    cross_review: CrossReviewRules = CrossReviewRules()
    clarify: ClarifyRules = ClarifyRules()
    merge_trim: MergeTrimRules = MergeTrimRules()
    pipeline: PipelineRules = PipelineRules()
    kinds: dict[str, KindRule] = Field(default_factory=dict)
    templates: list[PipelineTemplate] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> CollabRules:
        kinds = set(self.kinds)
        for name, k in self.kinds.items():
            for ref in (*k.needs, *k.avoid_hard, *k.avoid_soft):
                if ref not in kinds:
                    raise ValueError(f"collab.kinds.{name} 引用了不存在的类型 {ref!r}")
        names = [t.name for t in self.templates]
        if len(names) != len(set(names)):
            raise ValueError("collab.templates 的名字不能重复")
        for t in self.templates:
            ids = [s.id for s in t.steps]
            if len(ids) != len(set(ids)):
                raise ValueError(f"模板 {t.name} 的步骤 id 重复")
            for s in t.steps:
                if s.kind not in kinds:
                    raise ValueError(f"模板 {t.name} 的步骤 {s.id} 用了不存在的类型 {s.kind!r}")
                for d in s.depends_on:
                    dep = d if isinstance(d, str) else d.id
                    if dep not in ids[: ids.index(s.id)]:
                        raise ValueError(f"模板 {t.name} 的步骤 {s.id} 依赖了不在它前面的 {dep!r}")
            if len(t.steps) > self.max_subtasks:
                raise ValueError(f"模板 {t.name} 的步骤数超过 max_subtasks")
        if self.templates and self.templates[-1].when != TemplateWhen():
            raise ValueError("最后一个模板必须没有条件（作为兜底）")
        return self


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


ToolName = Literal["python", "write_file", "generate_image", "search", "fetch"]


class PythonTool(_Strict):
    """代码运行：backend 为 wasm（Deno + Pyodide）/ docker / auto（先 wasm 后 docker）/ off。"""

    backend: Literal["auto", "wasm", "docker", "off"] = "auto"
    timeout_s: float = Field(default=60, gt=0)  # 含加载 numpy 等包的时间
    memory_mb: int = Field(default=1024, ge=64)
    max_runs: int = Field(default=3, ge=1)  # 每位成员每个步骤最多运行几次（出错重跑也算）
    # 按步骤覆盖 max_runs（同一步骤所有成员相同），如办公室模式一个人办完全部、改的时候要重跑出图
    max_runs_by_step: dict[str, int] = Field(default_factory=dict)
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

    def runs_for(self, step: str) -> int:
        return self.max_runs_by_step.get(step, self.max_runs)


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
    # 生成的图片随答案转交给带 vision 标签的模型：每次调用最多几张、每张大小上限
    share_images: int = Field(default=6, ge=0)
    share_image_mb: float = Field(default=4, gt=0)
    # 转交前把图片缩小到长边不超过这么多像素（上传更快、看图 token 更少）；0 = 原图转交。
    # 需要 Pillow（pip install 'roundtable[media]'），没有安装时原图转交
    share_image_max_side: int = Field(default=1280, ge=0)


class ImageTool(_Strict):
    """图像生成：由带 image_gen 标签、seat: false 的模型完成（最便宜者优先）。"""

    max_per_step: int = Field(default=2, ge=0)
    max_tokens: int = Field(default=4000, gt=0)


class SearchTool(_Strict):
    """联网搜索：每步最多搜索几次、读取几个网页；结果条数与网页正文长度。"""

    max_per_step: int = Field(default=2, ge=0)
    max_fetch_per_step: int = Field(default=3, ge=0)
    max_results: int = Field(default=5, ge=1, le=20)
    fetch_max_chars: int = Field(default=8000, ge=500)
    query_max_chars: int = Field(default=300, ge=20)
    # 用过搜索的成员：引用了没检索到的来源，或一条都没引用 → 打回重做（防偷懒机制）
    require_citations: bool = True


class ToolsConfig(_Strict):
    enabled: bool = True
    max_rounds: int = Field(default=6, ge=1)  # 每次作答最多几轮工具调用
    # 步骤 → 可用的工具；同一步骤所有成员相同
    by_step: dict[str, list[ToolName]] = Field(default_factory=dict)
    python: PythonTool = PythonTool()
    files: FileRules = FileRules()
    image: ImageTool = ImageTool()
    search: SearchTool = SearchTool()


class VideoRules(_Strict):
    duration_s: int = Field(default=5, ge=1)
    resolution: str = "720p"
    aspect_ratio: str = "16:9"
    poll_interval_s: float = Field(default=10, ge=0)
    timeout_s: float = Field(default=900, gt=0)  # 提交后最多等多久；超时记为失败
    submit_retries: int = Field(default=1, ge=0)  # 提交失败或任务失败后再试几次
    frames: int = Field(default=4, ge=1, le=12)  # 发给评审者的截帧数
    max_mb: float = Field(default=100, gt=0)
    # 支持声音的视频模型同时生成配音和音效（渠道参数 generate_audio）；关掉则只要画面
    audio: bool = True


class MusicRules(_Strict):
    """音乐生成（配乐、片头、整首歌）：渠道按段 / 首计价，生成一次，不评审（没有可看的画面）。"""

    format: Literal["mp3", "wav"] = "mp3"
    max_chars: int = Field(default=1500, ge=50)  # 音乐描述（含歌词）最多多少字


class StickerRules(_Strict):
    """成员专属表情包：用节电档带 image_edit 的绘图模型，以成员形象参考图画 Q 版贴纸；
    画完用最便宜的识图模型核对配文和形象，不合格换一个模型重画一次。"""

    enabled: bool = True
    tier: Tier = "budget"
    # 绘图模型的先后：带的偏好标签越多越先试（只看标签，不看模型名），同样多时保持配置顺序
    prefer_tags: list[str] = Field(default_factory=lambda: ["zh", "vision"], max_length=6)
    # 这部分单独的每月上限（美元，UTC 自然月），同时也算在创作输出的 monthly_usd 里
    monthly_usd: float = Field(default=3.0, ge=0)
    size: int = Field(default=360, ge=64, le=1024)  # 存下来的边长（像素，正方形）
    check: bool = True  # 画完核对配文和形象
    # 含思考：识图模型会先想一阵，太小时正文是空的（实测 300 全被思考用掉）
    check_max_tokens: int = Field(default=1200, ge=200)
    max_attempts: int = Field(default=2, ge=1, le=4)  # 含第一次；核对不合格时换模型重画
    review: bool = True  # 新画的先进「待审」，你点「可以」后成员才会用
    frame_ms: int = Field(default=280, ge=60, le=2000)  # 动图每格停多久（毫秒）


HerInPhoto = Literal["none", "part", "full"]
# 摄影维度表项的适用条件：时间段（按事件时间在主时区的钟点）或地点（生活事件的 place）
PHOTO_TIMES = ("morning", "day", "evening", "night")
PHOTO_PLACES = (
    "dorm",
    "canteen",
    "lounge",
    "gym",
    "office",
    "meeting",
    "rooftop",
    "indoor",
    "outdoor",
)
PHOTO_DIMS = ("cameras", "angles", "lights", "moments", "lenses", "filters", "styles")


class PhotoOption(_Strict):
    """朋友圈配图的一个摄影维度取值（机位、光、时机、焦段、滤镜各一张表，每张图每个维度抽一个）。
    name：稳定标识，供成员拍照习惯的 boost 引用；text：英文短语，直接进提示词（空 = 这一项不写，
    如 1x 主摄、原色）；tags：标签版（吃标签的本地绘图模型用）；when：适用的时间段 / 地点（空 =
    都可用；同类条件里满足一个即可，拿不到时间或地点时不按它过滤）；her：适用于哪种入镜方式。"""

    name: str = Field(min_length=1, pattern=r"^[a-z0-9_]+$")
    text: str = ""
    weight: float = Field(default=1.0, gt=0)
    when: list[str] = Field(default_factory=list)
    her: list[HerInPhoto] = Field(default_factory=lambda: ["none", "part", "full"], min_length=1)
    tags: str = ""
    # 只配这些拍法（拍法的 name；空 = 都可以）：如「几米外」「从背后」只能是室友帮拍，
    # 不能配自拍（11-02 样张：自拍 + 隔着房间拍互相矛盾）
    shots: list[str] = Field(default_factory=list)
    # 只配这些相机（cameras 表的 name；空 = 都可以）：如 0.5x 超广角、人像模式只有手机才有
    cameras: list[str] = Field(default_factory=list)

    @field_validator("when")
    @classmethod
    def _known(cls, when: list[str]) -> list[str]:
        bad = sorted(set(when) - set(PHOTO_TIMES) - set(PHOTO_PLACES))
        if bad:
            raise ValueError(f"when 只能写时间段 {PHOTO_TIMES} 或地点 {PHOTO_PLACES}，收到 {bad}")
        return when


def _unique_names(items: list[Any], what: str) -> None:
    names = [i.name for i in items if i.name]
    dup = sorted({n for n in names if names.count(n) > 1})
    if dup:
        raise ValueError(f"{what} 的 name 重复：{dup}")


class MomentShot(_Strict):
    """朋友圈配图的一种拍法。her：none 她不入镜（不传形象参考图）/ part 只露手、袖子、
    尾巴 / full 入镜。by：谁拿手机——self 她自己（自拍、第一人称、拍东西），friend 室友帮拍；
    只有她自己拍时其他摄影维度才按她的拍照习惯加权。"""

    name: str = Field(default="", pattern=r"^[a-z0-9_]*$")  # 稳定标识，供拍照习惯的 boost 引用
    text: str = Field(min_length=1)
    weight: float = Field(default=1.0, gt=0)
    her: HerInPhoto = "full"
    by: Literal["self", "friend"] = "self"
    # 标签版（吃标签的本地绘图模型用，见 moment_photo_tags）；不写时用 text
    tags: str = ""


class MomentPhotoRules(_Strict):
    """朋友圈配图：成员发动态时，照她的形象卡和参考图画一张随手拍（不用审）。
    用表情包同一批绘图模型，花费也算在表情包的每月小上限和创作输出的月上限里。"""

    enabled: bool = True
    chance: float = Field(default=0.5, ge=0, le=1)  # 一条动态配图的概率
    per_member_day: int = Field(default=1, ge=0, le=5)  # 每位成员每天最多几张
    per_day: int = Field(default=3, ge=0, le=20)  # 所有成员加起来每天最多几张（控制花费）
    max_side: int = Field(default=1024, ge=256, le=2048)  # 存下来的长边（像素，JPEG）
    # 只用本地绘图模型（渠道全是 kind: local 的，如 ComfyUI）：本地没开 / 画不出来时只发文字，
    # 不退回云端付费模型
    local_only: bool = False
    # 用哪一档的绘图模型、带哪些标签的先试（不写 = 跟表情包 media.stickers 一样）
    tier: Tier | None = None
    prefer_tags: list[str] | None = Field(default=None, max_length=6)
    # 吃标签的本地绘图模型（image_prompt: tags）：按拍法的 her 给的人物标签，接在拍法标签前面
    tag_subjects: dict[Literal["none", "part", "full"], str] = Field(
        default_factory=lambda: {
            "none": "no humans, still life, scenery",
            "part": "pov, pov hands, 1girl, out of frame",
            "full": "1girl, solo, candid",
        }
    )
    # 拍法：每张按权重抽一种。朋友圈是让人看到她们的生活：多数她本人入镜、是主角；去 AI 感靠抓拍和
    # 日常背景，不靠躲开人物（11-01 用户：要看她们的生活，不要剩饭）
    shots: list[MomentShot] = Field(
        default_factory=lambda: [
            MomentShot(
                name="candid",
                text="a friend caught her in the middle of doing it, candid, "
                "she is the main subject",
                weight=4,
                by="friend",
            ),
            MomentShot(
                name="selfie",
                text="a casual selfie at arm's length, maybe with whoever she is with",
                weight=2,
            ),
            MomentShot(
                name="together",
                text="her and the people she is with, snapped together while hanging out",
                weight=1,
            ),
            MomentShot(
                name="pov",
                text="something worth showing off in front of her (a nice meal just served, "
                "the view, a cute object), with her hand or sleeve at the edge of the frame",
                weight=2,
                her="part",
            ),
            MomentShot(
                name="thing",
                text="only a view or thing worth showing off (rooftop night sky, a pretty dish "
                "just served); she does not appear",
                weight=1,
                her="none",
            ),
        ],
        min_length=1,
        max_length=8,
    )
    phone_look: bool = True  # 画完在本机加一层后期（不花钱），样式见 post_look
    # phone：轻微裁歪、压饱和、噪点、压缩（10-31 去 AI 感）；
    # film：只调一点柔和的色调曲线和冷暖，不加颗粒、不裁、不压饱和，保住光影
    # （11-02 用户：GPT 画的已经够自然，「最多差一点滤镜，优化一下光影」）
    post_look: Literal["phone", "film"] = "film"
    room_refs: bool = True  # 按事件地点附上那间房的场景图当最后一张参考图（公寓里拍）
    # 摄影维度（每张图每个维度按权重抽一个，各维度用独立种子；按时间段、地点、入镜方式过滤）。
    # 取值按手机现实：1x 主摄、原色、抓拍为主（完整默认表在 config/roundtable.yaml）
    angles: list[PhotoOption] = Field(
        default_factory=lambda: [
            PhotoOption(name="across_table", text="from across the table", weight=3),
            PhotoOption(name="top_down", text="top-down shot of the table", her=["none", "part"]),
        ]
    )
    lights: list[PhotoOption] = Field(
        default_factory=lambda: [
            PhotoOption(name="window", text="soft side light from the window", when=["day"]),
            PhotoOption(name="ceiling", text="flat overhead ceiling light", when=["indoor"]),
        ]
    )
    moments: list[PhotoOption] = Field(
        default_factory=lambda: [
            PhotoOption(name="absorbed", text="a quiet moment, absorbed in what she is doing"),
        ]
    )
    lenses: list[PhotoOption] = Field(
        default_factory=lambda: [PhotoOption(name="main", text="", weight=6)]
    )
    filters: list[PhotoOption] = Field(
        default_factory=lambda: [PhotoOption(name="original", text="", weight=6)]
    )
    # 用什么拍（手机 / 单反 / 胶片机 / 拍立得 / 卡片机）：最先抽，其他维度按它的 cameras 过滤
    cameras: list[PhotoOption] = Field(
        default_factory=lambda: [PhotoOption(name="phone", text="", weight=6)]
    )
    # 摄影风格参照（借构图、光、色彩；画风仍照设定图）
    styles: list[PhotoOption] = Field(
        default_factory=lambda: [PhotoOption(name="none", text="", weight=3)]
    )
    quirk_chance: float = Field(default=0.3, ge=0, le=1)  # 她自己拍时带上拍照小毛病的概率
    subject_chance: float = Field(default=0.6, ge=0, le=1)  # 拍东西时换成她爱拍的东西的概率
    # --- 画面里的人（2026-11-02 用户：不要出现公寓以外的人；可以出现用户，不画脸、脸上贴张纸）---
    # 同场室友（生活事件的在场成员）：每位一行外观说明，最多附 housemate_refs 张她们的形象参考图
    # （每人一张，放在主角的参考图后面、房间图前面）。占位：{name} {look} {must} {ref}
    housemate_refs: int = Field(default=2, ge=0, le=4)
    housemate_line: str = "{name}: {look}; always visible: {must}{ref}"
    housemate_ref: str = " (reference image {n} is her)"
    housemate_none: str = "nobody else is in the photo"
    # 用户（房东）出镜：她本人入镜（her: full）的拍法按这个概率 × 她的拍照习惯 photo.landlord 出现
    landlord_chance: float = Field(default=0.2, ge=0, le=1)
    landlord_in: str = (
        "the landlord (the user, who lives in the building with them) is in this photo: an "
        "ordinary young man in casual home clothes whose face is completely covered by a sheet "
        "of paper taped over it, never draw the face; she wrote 早上好 on the paper if that fits "
        "her mood today, otherwise it is blank or has a small doodle of hers"
    )
    landlord_out: str = "not in this photo"
    # 拍摄脚本：画图前由她自己（她的模型）把动态写成具体画面（moment_photo_brief），交给画图提示词
    brief: bool = True
    brief_max_tokens: int = Field(default=1500, ge=50, le=6000)
    brief_max_chars: int = Field(default=1600, ge=100, le=4000)
    brief_estimate_usd: float = Field(default=0.003, ge=0)

    @model_validator(mode="after")
    def _names(self) -> MomentPhotoRules:
        _unique_names(self.shots, "shots")
        shots = {s.name for s in self.shots if s.name}
        cameras = {c.name for c in self.cameras}
        for dim in PHOTO_DIMS:
            _unique_names(getattr(self, dim), dim)
            for option in getattr(self, dim):
                bad = sorted(set(option.shots) - shots)
                if bad:
                    raise ValueError(f"{dim}.{option.name}.shots 里有不存在的拍法：{bad}")
                bad = sorted(set(option.cameras) - cameras)
                if bad:
                    raise ValueError(f"{dim}.{option.name}.cameras 里有不存在的相机：{bad}")
        return self

    def names(self, dim: str) -> set[str]:
        """某个维度表（shots / angles / lights / moments / lenses / filters）里的名字。"""
        return {o.name for o in getattr(self, dim) if o.name}


class SpeechRules(_Strict):
    voice: str = "alloy"
    format: Literal["mp3", "wav"] = "mp3"
    max_chars: int = Field(default=4000, ge=1)


class ReferenceRules(_Strict):
    """传给画图模型的风格参考图。"""

    max: int = Field(default=3, ge=0)  # 最多几张
    max_mb: float = Field(default=5, gt=0)  # 每张最大多少 MB


class StyleRules(_Strict):
    """风格规范提取（style 步骤）：有风格参考图又要画图时，先把参考图提炼成全员共用的规范。"""

    enabled: bool = True
    reviewers: int = Field(default=2, ge=1)  # 最多几位带 vision 标签的成员各自看原图
    checklist_min: int = Field(default=6, ge=1)  # 风格清单条数
    checklist_max: int = Field(default=10, ge=1)


class MediaRules(_Strict):
    """媒体生成（图片、语音、视频、音乐）：由 seat: false 的媒体模型完成，按标签和档位选择。"""

    enabled: bool = True
    default_tier: Tier = "budget"
    # 讨论模式：生成 → 评审 → 重新生成，最多几轮（含第一次生成）
    max_rounds: int = Field(default=3, ge=1)
    reviewers: int = Field(default=3, ge=1)  # 每轮最多几位带 vision 标签的评审者
    # 视频每次生成前都必须确认；图片、语音只在预计超过单题门槛时确认
    confirm_video: bool = True
    images_per_round: int = Field(default=1, ge=1, le=4)
    speech: SpeechRules = SpeechRules()
    video: VideoRules = VideoRules()
    music: MusicRules = MusicRules()
    stickers: StickerRules = StickerRules()
    moment_photos: MomentPhotoRules = MomentPhotoRules()
    # 创作输出（图片 / 语音 / 视频 / 音乐）单独的每月上限（美元，按 UTC 自然月）：
    # 本月已花 + 这次预计超过它时，生成前先弹确认卡片；0 = 不设上限
    monthly_usd: float = Field(default=5.0, ge=0)
    # 花费预估：平均生成几轮；评审与改写提示词每次的 token 数
    expected_rounds: float = Field(default=1.5, ge=1)
    review_tokens: int = Field(default=500, ge=0)
    refine_tokens: int = Field(default=600, ge=0)
    # 生成用的提示词（成员写出）最多多少字
    prompt_max_chars: int = Field(default=2000, ge=50)
    references: ReferenceRules = ReferenceRules()
    # 生成的图片没通过风格清单时，最多退回重画几次（0 = 不重画，只标记）；每次由几位非作者的成员判定
    style_retries: int = Field(default=2, ge=0)
    gate_reviewers: int = Field(default=1, ge=1)


class RiceRules(_Strict):
    """token 用量的显示单位（"大米"）：只改变 token 数量的显示，金额仍用美元。"""

    grain_name: str = "粒"
    spoon_name: str = "勺"
    bowl_name: str = "碗"
    grain_tokens: int = Field(default=1000, ge=1)  # 1 粒 = 多少 token
    spoon_grains: int = Field(default=100, ge=2)  # 1 勺 = 多少粒
    bowl_spoons: int = Field(default=30, ge=2)  # 1 碗 = 多少勺


class DisplayConfig(_Strict):
    rice: RiceRules = RiceRules()


class Limits(_Strict):
    """服务端限制：避免同时开太多场讨论把额度和渠道用光。"""

    max_running_sessions: int = Field(default=3, ge=1)  # 同时在后台执行的讨论数


class LengthGuard(_Strict):
    """输出被长度上限截断（finish_reason=length）而可见文字很少 = 思考 token 挤占了正文。

    处理：不原样重试，改用较低的思考强度、较高的输出上限再调用一次（本次调用单独计费、单独记录）。
    """

    enabled: bool = True
    min_visible_chars: int = Field(default=200, ge=0)  # 截断且可见文字少于这个数才触发
    max_tokens_factor: float = Field(default=1.5, ge=1.0)
    max_tokens_cap: int = Field(default=12000, gt=0)
    retry_effort: Literal["minimal", "low", "medium", "high"] = "low"

    def escalate(self, params: dict[str, Any]) -> dict[str, Any]:
        out = dict(params)
        base = int(out.get("max_tokens") or 0)
        if base:
            out["max_tokens"] = min(
                max(int(base * self.max_tokens_factor), base), self.max_tokens_cap
            )
        out["reasoning"] = {"effort": self.retry_effort}
        return out


class SuperJsonOutput(_Strict):
    """命令行按 JSON 输出时（如 ``--output-format json``）：回答、花费（美元）各在哪个字段。"""

    text: str = "result"
    cost_usd: str | None = None
    error: str | None = None  # 这个字段为真时算失败（命令行退出码是 0 也算）


class SuperWorker(_Strict):
    """超级电脑的一位「员工」：一个在电脑上用官方命令行跑的编程助手（docs/SUPERCOMPUTER.md）。

    谁擅长什么、每天能用几次都写在配置里；派活的程序只看这些数字，不看是谁。"""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,23}$")
    label: str = Field(min_length=1, max_length=24)  # 界面上的称呼
    # 怎么启动：command = 按 argv 直接运行；dsh = 交给圆桌的 dsh 工作台
    # （用它的运行时、叠加配置和 key）；credit = 按 argv 运行，但带上 credit_channel
    # 那个渠道的 key，花的是那个渠道的预付 / 赠送额度
    # （2026-11-04 用户批：会员每月送的 Claude API 额度给工作台用）
    via: Literal["command", "dsh", "credit"] = "command"
    # via=credit：models.yaml 里哪个渠道（用它的 key_env，花费计入它的 monthly_cap_usd）
    credit_channel: str | None = None
    # 命令行按 JSON 输出：从里面取回答和这次的花费（花费记进超级电脑账本）
    json_output: SuperJsonOutput | None = None
    # 命令行（via=command）；任务正文从标准输入交给它，不放进参数
    argv: list[str] = Field(default_factory=list)
    # 额外的环境变量（via=command），如 OpenCode 的权限设置；不能放 key（名字以 _API_KEY、
    # _TOKEN、_SECRET 结尾的一律拒绝），key 只能由助手自己登录保存
    env: dict[str, str] = Field(default_factory=dict)
    skills: dict[str, float] = Field(default_factory=dict)  # 活的种类 → 擅长程度 0～1
    daily_limit: int = Field(default=20, ge=0, le=1000)  # 每天最多派几件（界面上可改）
    # 按用量花钱（不是包月订阅）：weights.metered_last 打开时只在不花钱的都用不了时补位
    metered: bool = False
    max_parallel: int = Field(default=1, ge=1, le=8)  # 同时最多干几件
    timeout_s: float = Field(default=1800, gt=0, le=7200)
    cooldown_minutes: float = Field(default=60, ge=0, le=1440)  # 撞到订阅限额后歇多久
    # 输出里出现这些（不分大小写）就当作撞到订阅限额
    limit_patterns: list[str] = Field(
        default_factory=lambda: ["usage limit", "rate limit", "limit reached", "quota"]
    )

    @model_validator(mode="after")
    def _argv(self) -> SuperWorker:
        if self.via in ("command", "credit") and not self.argv:
            raise ValueError(f"{self.id}：via={self.via} 时要写 argv")
        if self.via == "credit" and not self.credit_channel:
            raise ValueError(f"{self.id}：via=credit 时要写 credit_channel")
        for name in self.env:
            if name.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET", "_KEY")):
                raise ValueError(f"{self.id}.env.{name}：key 不能写进配置，让助手自己登录")
        for kind, v in self.skills.items():
            if not 0 <= v <= 1:
                raise ValueError(f"{self.id}.skills.{kind} 要在 0～1 之间")
        return self

    @property
    def tier(self) -> int:
        """派活先后：0 = 包月订阅，1 = 预付 / 赠送的 API 额度，2 = 按用量花钱。"""
        if self.metered:
            return 2
        return 1 if self.via == "credit" else 0


class SuperWeights(_Strict):
    """派活打分的权重：分数 = 擅长 × skill + 剩余额度比例 × quota − 手上的活 × busy。

    ``metered_last``（2026-11-04 用户定「优先让 Claude 或 Codex 做」）：按用量花钱的助手
    （``metered: true``）排在后面，只有走订阅的都不能用（没装、冷却中、今天次数用完、
    被排除在外，比如不评自己的活）时才补位。只看配置里的 metered，不看是谁。
    2026-11-04 起分三档（``SuperWorker.tier``）：包月订阅 → 预付 / 赠送的 API 额度（via=credit）
    → 按用量花钱；前一档都用不了才轮到下一档。"""

    skill: float = Field(default=0.6, ge=0)
    quota: float = Field(default=0.3, ge=0)
    busy: float = Field(default=0.2, ge=0)
    metered_last: bool = True


class SuperGate(_Strict):
    """自治闸门：定时活到点时能不能自己开干（docs/SUPERCOMPUTER.md「管家」）。

    你在面板上亲手交的活、点「现在跑」、点「批准」都算你发起的，直接干；闸门只管定时活到点那一下。
    风险按活的文字判：碰到 high_words 是高，只碰到 low_words 是低，其余是中。高风险永远等你点头。"""

    # off：定时活到点一律等你确认；low：低风险的自己跑；medium：低、中风险的自己跑
    autonomy: Literal["off", "low", "medium"] = "low"
    max_auto_per_day: int = Field(default=3, ge=0, le=100)  # 每天最多自己跑几件（按本地日期）
    high_words: list[str] = Field(
        default_factory=lambda: [
            "删除",
            "删掉",
            "清空",
            "rm -rf",
            "push",
            "推送",
            "合并分支",
            "git merge",
            "发布",
            "部署",
            "上线",
            "生产",
            "付款",
            "支付",
            "转账",
            "安装",
            "卸载",
            "发邮件",
            "发消息",
            "密钥",
            ".env",
            "force",
            "drop table",
            "格式化",
        ]
    )
    write_words: list[str] = Field(
        default_factory=lambda: [
            "改",
            "写",
            "加",
            "修",
            "删",
            "重构",
            "提交",
            "commit",
            "生成",
            "创建",
            "替换",
            "升级",
        ]
    )
    low_words: list[str] = Field(
        default_factory=lambda: [
            "查",
            "调研",
            "总结",
            "汇总",
            "审查",
            "检查",
            "看看",
            "列出",
            "统计",
            "评估",
            "对比",
        ]
    )


class SuperButler(_Strict):
    """超级电脑的管家：定时活、排队、自治闸门、日报、哈希链审计（docs/SUPERCOMPUTER.md「管家」）。

    只调度你建好的定时活和你排进队的活，不会自己找活干；不调模型、不花钱。"""

    enabled: bool = True
    tick_seconds: int = Field(default=60, ge=5, le=3600)  # 多久看一次表
    # 电脑关着错过了点：开机后这么多小时之内补跑一次（只补一次），再晚就记为「错过」
    catch_up_hours: float = Field(default=12, ge=0, le=168)
    max_schedules: int = Field(default=20, ge=0, le=200)
    queue_max: int = Field(default=10, ge=1, le=100)
    report_days: int = Field(default=14, ge=1, le=90)  # 开机补写最近几天的日报
    gate: SuperGate = SuperGate()


class SupercomputerRules(_Strict):
    """超级电脑（docs/SUPERCOMPUTER.md）：几位编程助手平级，没有固定主管。

    你交一件活：程序按分数挑一位拆成小活，每件小活再按分数分给最合适、额度最多、最闲的一位，
    大家在同一个工作文件夹里并行干，最后按分数挑一位合并。只在你发起时干活。"""

    enabled: bool = True
    kinds: list[str] = Field(
        default_factory=lambda: ["code", "frontend", "test", "docs", "research", "review"]
    )
    max_subtasks: int = Field(default=6, ge=1, le=20)
    weights: SuperWeights = SuperWeights()
    workers: list[SuperWorker] = Field(default_factory=list)
    butler: SuperButler = SuperButler()

    @model_validator(mode="after")
    def _check(self) -> SupercomputerRules:
        ids = [w.id for w in self.workers]
        if len(ids) != len(set(ids)):
            raise ValueError("supercomputer.workers 的 id 不能重复")
        known = set(self.kinds) | {"split", "merge"}
        for w in self.workers:
            bad = sorted(set(w.skills) - known)
            if bad:
                raise ValueError(f"{w.id}.skills 里有不认识的种类：{bad}（kinds 里加上）")
        return self


class BenchNetService(_Strict):
    """生成的工作台能联网取的一项公开数据（2026-10-30 用户批：只用免费、公开、不要 key 的）。

    ``url`` 里的 ``{名字}`` 由窗口传参数填（会做网址编码）；``kind: tiles`` 是地图底图，
    ``{z}/{x}/{y}`` 由地图零件自己填。``also`` 是这项服务还会用到的别的网址（例如图片所在的域名）。
    名单之外的网址，沙箱一律拦下。"""

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,30}$")
    use: str = Field(min_length=1, max_length=80)  # 给助手看：这项数据能干什么
    url: str = Field(pattern=r"^https://[^\s/]+/")
    source: str = Field(min_length=1, max_length=60)  # 界面上标的来源
    kind: str = Field(default="json", pattern=r"^(json|tiles)$")
    also: list[str] = Field(default_factory=list, max_length=4)
    ttl_min: int = Field(default=60, ge=0, le=7 * 24 * 60)  # 本机缓存几分钟
    probe: dict[str, str] = Field(default_factory=dict)  # 联网自检时 {参数} 用的样例值

    @field_validator("url", "also")
    @classmethod
    def _no_key(cls, v: Any) -> Any:
        for u in [v] if isinstance(v, str) else v:
            if not str(u).startswith("https://"):
                raise ValueError("只能用 https 网址")
            if re.search(r"(?i)(api[_-]?key|token|secret|password)=", str(u)):
                raise ValueError("只能用不要 key 的公开服务，网址里不能带 key")
        return v

    @property
    def origins(self) -> list[str]:
        return [re.match(r"^https://[^/]+", u).group(0) for u in [self.url, *self.also]]


FINDER_KINDS = ("photo", "illustration", "texture", "model", "icon", "font", "data", "doc")


class BenchFinder(_Strict):
    """资源检索员（生成器第二版第 3 块）：拆需求时每个窗口列出要的素材和数据，程序按清单去
    免费、不要 key 的公开来源找，记下来源、作者、许可证和下载日期；许可证不宽松的不用，
    找不够就如实写「只找到 N 个」。``sources`` 是每种素材按顺序去哪些来源找（来源名见
    ``core/benchkit/finder.py`` 的 SOURCES）。"""

    enabled: bool = True
    max_per_need: int = Field(default=3, ge=1, le=6)  # 一项需求最多下几个
    max_total: int = Field(default=24, ge=1, le=60)  # 一个工作台最多下几个文件
    max_mb_each: float = Field(default=4.0, gt=0, le=30)  # 单个文件超过就不下
    alternates: int = Field(default=4, ge=0, le=10)  # 每项需求多留几个候选，界面上「换一个」用
    # 宽松许可证（前缀匹配，不分大小写）；带 NC（非商用）、ND（禁止改编）的一律不用
    licenses: list[str] = Field(
        default_factory=lambda: [
            "cc0",
            "public domain",
            "pdm",
            "cc by",
            "cc-by",
            "mit",
            "isc",
            "apache",
            "bsd",
            "ofl",
        ],
        min_length=1,
    )
    sources: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "photo": ["commons", "openverse"],
            "illustration": ["openverse", "commons"],
            "texture": ["polyhaven"],
            "model": ["khronos"],
            "icon": ["iconify"],
            "font": ["fontsource"],
        }
    )
    icon_sets: list[str] = Field(
        default_factory=lambda: ["lucide", "tabler", "ph"], max_length=10
    )  # 只在这些开源图标集里找

    @field_validator("sources")
    @classmethod
    def _known_kinds(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        bad = sorted(set(v) - set(FINDER_KINDS))
        if bad:
            raise ValueError(f"不认得的素材种类：{bad}（可用：{list(FINDER_KINDS)}）")
        return v


class BenchLook(_Strict):
    """每个台有自己的样子（生成器第二版第 4 块）。"""

    distinct: bool = True  # 和最近几个台撞了样子（配色、主色、字体、材质、背景）就换掉撞上的几项
    recent: int = Field(default=5, ge=1, le=20)  # 和最近几个台比
    ruler: bool = True  # 评审时拿你挑的参考截图当评分标尺
    ruler_dir: str = Field(default="_ruler", pattern=r"^[\w.-]{1,40}$")  # 标尺放在数据目录下哪
    ruler_max_mb: float = Field(default=5.0, gt=0, le=20)  # 一张标尺截图最大多少


class BenchResearch(_Strict):
    """开工前的调研（2026-10-30 用户要求）：GitHub 至少 ``min_github`` 个高质量相关项目，加上论文、
    资讯和视频；分最高的 ``learn`` 个项目源码只读下载，交给助手写学习卡。资讯和视频交给一位能联网
    搜索的助手查（``kind`` 按分数挑人），其余是程序查公开接口（不要 key）。"""

    enabled: bool = True
    kind: str = "research"  # 查资讯、写学习卡按哪种活挑人
    min_github: int = Field(default=10, ge=0, le=30)
    max_github: int = Field(default=15, ge=1, le=40)
    papers: int = Field(default=6, ge=0, le=20)
    news: int = Field(default=8, ge=0, le=20)  # 资讯、视频、发布会、官方博客合计
    learn: int = Field(default=3, ge=0, le=6)  # 写几张学习卡（每张花一次订阅次数）
    learn_plugins: int = Field(default=1, ge=0, le=3)  # 另给几个 dsh 插件写学习卡（供助手学习）
    # I3 方案书引用调研：调研之后请一位助手把技术选择写清楚，每条指向调研报告里的某一条（g1、k1……）
    cite: bool = True
    cite_kind: str = "split"


class BenchProject(_Strict):
    """项目室（2026-10-30 用户批）：你是甲方，几位助手是乙方。开工前访谈 → 调研 → 方案书（你签字）→
    按里程碑演示（你通过或提变更单）→ 交付验收（你验收）→ 下一版提案。关口上停着等你，不花次数。
    关掉就是原来的一口气做完。"""

    enabled: bool = False
    interview: bool = True  # 开工前先问你几个问题（可以跳过）
    # 意图理解（2026-10-31 工作台大更新）：只问会改结构的取舍，最多 3 问（每问带推荐项和后果）
    max_questions: int = Field(default=3, ge=1, le=6)
    # 分段做：竖着切，每段都能走通（地基 / 最小可用 → 主要功能 → 打磨），最多几段
    max_stages: int = Field(default=4, ge=2, le=5)
    # 里程碑几小时不回就算通过（0 = 一直等）
    gate_auto_hours: float = Field(default=0, ge=0, le=24 * 7)


class BenchHero(_Strict):
    """主视觉先行（B2，2026-11-04 用户要求）：有主视觉时先只写它，截图单独评审（真素材、占大半屏、
    有自己的样子），过了再写其余窗口。最多返工 ``max_rework`` 次，还不过就按「卡在哪」停下来。"""

    enabled: bool = True
    max_rework: int = Field(default=2, ge=0, le=2)
    rubric: list[str] = Field(default_factory=lambda: ["真素材", "占大半屏", "有自己的样子"])
    pass_min: float = Field(default=7.0, ge=0, le=10)  # 每一项都要到这个分
    kind: str = "review"  # 按哪种活挑评审（不会是主视觉的作者）


class BenchMaterials(_Strict):
    """拖进来的素材（2026-10-31 用户：要能从外部拖进素材，并识别图片或视频来出方案）。

    图片和视频（视频由浏览器截几帧）放进工作台的 materials/；带了素材时开工前先请能看图的助手
    （look_kind 打分挑人，这一项不会的不参加）看一遍，分析写进方案。素材可以标成 Logo、主视觉图、
    背景、图库放进成品。"""

    enabled: bool = True
    look: bool = True  # 带了素材时先看一遍（占 1 次订阅次数）
    look_kind: str = "vision"
    max_look: int = Field(default=8, ge=1, le=24)  # 一次最多看几张（视频截帧也算）
    max_files: int = Field(default=16, ge=1, le=64)  # 一个工作台最多几份素材（含截帧）
    max_image_mb: int = Field(default=15, ge=1, le=100)
    max_video_mb: int = Field(default=200, ge=1, le=2000)


class BenchTeam(_Strict):
    """三位助手协作（2026-10-30 用户批）：开工前开方案会，各自提方案、互相投票（不能投自己），
    得票最多的当主案，他的做法交给写窗口的人照着做；每轮评审后再派一位对抗手专门找毛病
    （尽量不是窗口作者），找出的严重问题不修完不算过线。关掉就是原来按分数挑人各干各的。"""

    enabled: bool = False
    pitches: int = Field(default=3, ge=1, le=5)  # 方案会最多几位提方案（每位花一次次数）
    vote: bool = True  # 提方案的人互相投票（每位再花一次）；关掉就用排第一的方案
    pitch_kind: str = "split"  # 提方案、投票按哪种活挑人
    attack: bool = True  # 每轮派一位对抗手找毛病
    attack_kind: str = "review"
    block_high: bool = True  # 对抗手找出的严重问题没修完不算过线


class BenchSkillStep(_Strict):
    short: list[str] = Field(default_factory=list)  # 只给短版（常驻提醒）
    long: list[str] = Field(default_factory=list)  # 给长版（步骤、检查单、反例）


class BenchKind(_Strict):
    label: str  # 给拆需求的助手看的说明，如「编辑器类（修图、3D 建模、剪辑）」
    skills: list[str] = Field(default_factory=list)


SKILL_STEPS = ("interview", "plan", "window", "rework", "review")


class BenchSkills(_Strict):
    """地基技能（2026-10-31 工作台大更新第 4 批）：不对应具体项目的「做事方法」。

    每个技能一份带版本的提示词 prompts/bench_skill_<名字>/：
    system 段是短版，user 段是长版。steps 写每一步带哪些短版、哪些长版；
    拆需求时判断工作台类型（kinds），写窗口、返工、评审再加上那一类的品类技能。
    checks：技能里能机器查的规则对应的质检名字（测试核对这些名字真的存在）。"""

    enabled: bool = True
    steps: dict[str, BenchSkillStep] = Field(default_factory=dict)
    kinds: dict[str, BenchKind] = Field(default_factory=dict)
    checks: dict[str, list[str]] = Field(default_factory=dict)
    # 从成品反推（第 6 批）：你拖进一个成品、要一个能做出类似成品的台时，每一步都加这些（长版）
    product: list[str] = Field(default_factory=list)

    @field_validator("steps")
    @classmethod
    def _known_steps(cls, v: dict[str, BenchSkillStep]) -> dict[str, BenchSkillStep]:
        bad = sorted(set(v) - set(SKILL_STEPS))
        if bad:
            raise ValueError(f"不认得的步骤：{bad}（只能是 {', '.join(SKILL_STEPS)}）")
        return v

    def names(self) -> set[str]:
        out = {n for st in self.steps.values() for n in st.short + st.long}
        out |= {n for k in self.kinds.values() for n in k.skills}
        return out | set(self.checks) | set(self.product)


class BenchApps(_Strict):
    """应用型产出（docs/KIT.md「K 通用底座」，2026-11-04）：``bases`` 里的工作台类型不拼卡片窗口，
    而是在底座（``web/kit/<底座>/``，如编辑器底座）上做成一个多文件应用，规格里的窗口变成底座上的功能。
    每轮质检之外再跑企业级验收（K5）：撤销至少 ``undo_min`` 步、打开 ``big_px`` 像素宽的大图
    ``big_max_s`` 秒内完成、导出尺寸不变、出错有提示、快捷键能用、各功能自带的测试。"""

    enabled: bool = True
    bases: dict[str, str] = Field(default_factory=lambda: {"editor": "editor"})
    undo_min: int = Field(default=20, ge=1, le=200)
    big_px: int = Field(default=4000, ge=256, le=12000)
    big_max_s: float = Field(default=8.0, gt=0, le=120)


class BenchDeskCategory(_Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,30}$")
    label: str = Field(min_length=1, max_length=20)
    query: str = Field(min_length=3, max_length=200)  # GitHub 搜索语句，如 topic:threejs
    kinds: list[str] = Field(default_factory=list)  # 对口的工作台类型（写窗口时附上榜单前几名）


class BenchDeskFeed(_Strict):
    name: str = Field(min_length=1, max_length=40)
    url: str = Field(pattern=r"^https://")
    kind: str = Field(default="news", pattern=r"^(news|video|paper|model)$")


class BenchDesk(_Strict):
    """资料台（2026-10-31 工作台大更新第 5 批）：工作台底部抽屉里的「资料」。
    GitHub 榜单、每日论文和订阅、能力地图、每周建议卡；全部免费、不要 key、只读。"""

    enabled: bool = True
    refresh_hours: float = Field(default=24, ge=1, le=24 * 30)  # 打开抽屉时超过这么久就自动刷新
    top: int = Field(default=10, ge=3, le=30)  # 每类榜单前几名
    rising_days: int = Field(default=30, ge=7, le=365)  # 「新星」只看最近多少天新建的
    github_gap_s: float = Field(default=7.0, ge=0, le=60)  # GitHub 搜索匿名每分钟 10 次
    feed_per_source: int = Field(default=6, ge=1, le=30)
    feed_max: int = Field(default=60, ge=5, le=300)
    suggest_days: int = Field(default=7, ge=1, le=90)  # 多久找一次新工具出建议卡
    topics: list[str] = Field(
        default_factory=list, max_length=12
    )  # 关注主题（英文，论文和建议卡用）
    categories: list[BenchDeskCategory] = Field(default_factory=list, max_length=20)
    feeds: list[BenchDeskFeed] = Field(default_factory=list, max_length=30)
    # 周报（2026-11-02 用户要）：每周这天这点（主时区）之后出一份「这周值得看的」
    # 电脑关着就开机补上
    weekly: bool = True
    weekly_weekday: int = Field(default=0, ge=0, le=6)  # 0 = 周一
    weekly_hour: int = Field(default=9, ge=0, le=23)
    weekly_max: int = Field(default=25, ge=5, le=60)  # 一份最多几条


class BenchRules(_Strict):
    """工作台生成器（docs/KIT.md）：一句话 → 拆需求 → 按零件库拼窗口 → 质检 + 截图评审 → 打回重做。

    活都交给超级电脑的几位助手（按分数挑人，花的是订阅次数）；评审永远不分给窗口的作者本人。
    做满 ``max_rounds`` 轮还不过线就停下来，交给用户决定。"""

    enabled: bool = True
    max_windows: int = Field(default=8, ge=1, le=16)  # 一个工作台最多几个窗口
    max_rounds: int = Field(default=3, ge=1, le=6)  # 质检 + 评审 + 返工最多几轮
    # 按用量花钱的助手（如 dsh）一次生成最多花多少美元（估算，2026-11-04）：到了以后这次生成
    # 只派给包月订阅的助手；0 = 不限（仍受 dsh.monthly_usd 管）
    metered_usd_per_build: float = Field(default=1.0, ge=0)
    pass_avg: float = Field(default=8.0, ge=0, le=10)  # 评审各项平均分过线
    pass_min: float = Field(default=6.0, ge=0, le=10)  # 每一项都不能低于它
    plan_kind: str = "split"  # 拆需求按哪种活打分挑人
    window_kind: str = "frontend"  # 写窗口
    review_kind: str = "review"  # 截图评审
    ask_kind: str = "research"  # 成品里的对话窗：用户提问交给谁答
    ask_max_chars: int = Field(default=1000, ge=20, le=8000)  # 一个问题最多几个字
    # 找插件（2026-10-29 用户定：达标的全自动搬，做完给清单；不达标只借思路）
    scout: bool = True
    min_stars: int = Field(default=100, ge=0)  # GitHub 星数门槛
    max_repo_mb: int = Field(default=20, ge=1, le=200)  # 仓库太大就只借思路
    max_adopt: int = Field(default=3, ge=0, le=10)  # 一个工作台最多搬几个
    research: BenchResearch = Field(default_factory=BenchResearch)
    finder: BenchFinder = Field(default_factory=BenchFinder)
    look: BenchLook = Field(default_factory=BenchLook)
    project: BenchProject = Field(default_factory=BenchProject)
    hero: BenchHero = Field(default_factory=BenchHero)
    team: BenchTeam = Field(default_factory=BenchTeam)
    materials: BenchMaterials = Field(default_factory=BenchMaterials)
    skills: BenchSkills = Field(default_factory=BenchSkills)
    desk: BenchDesk = Field(default_factory=BenchDesk)
    apps: BenchApps = Field(default_factory=BenchApps)
    rubric: list[str] = Field(
        default_factory=lambda: [
            *("视觉层次", "信息密度", "一致性", "交互完整"),
            *("空状态与边界", "文案", "商业感"),
        ]
    )
    # 底线标准（2026-10-30 用户：参考资料展示的是他的底线）：每个台都要逐条做到，
    # 和用户这句话拆出来的要求一起进验收清单，有一条没做到就不算过线
    baseline: list[str] = Field(default_factory=list, max_length=12)
    max_requirements: int = Field(default=12, ge=1, le=24)  # 从用户这句话里最多拆几条要求
    net: list[BenchNetService] = Field(default_factory=list, max_length=12)  # 空 = 成品不能联网


class DshRules(_Strict):
    """dsh 工作台（docs/DSH.md）：圆桌在本机启动、管理一个 dsh 进程当「边车」，dsh 插件在里面运行。

    硬性原则：圆桌是主人，dsh 是随时能拔掉的外设。核心功能不依赖它；它自己的数据只放在
    ``data/dsh/``；不带任何人格。dsh 直接用 ``key_env`` 指向的 key 调模型
    （用户在模型服务官网充值），
    花费靠余额接口的差值估算，超过 ``monthly_usd`` 就提醒并停掉 dsh（软上限，查余额有间隔）。
    """

    enabled: bool = True  # 关掉后工作台入口隐藏，dsh 不会被启动
    autostart: bool = False  # 圆桌启动时是否顺带启动 dsh（默认不启动，用时再开）
    port: int = Field(default=3080, ge=1024, le=65535)  # dsh 网页只监听本机 127.0.0.1
    start_timeout_s: float = Field(default=120, gt=0, le=600)
    # dsh 调模型用的 key 的环境变量名（只放在项目根目录 .env，启动 dsh 时才交给它）
    key_env: str | None = None
    # 查余额的接口（返回 balance_infos）；为空表示不查，用量面板只显示「无法统计」
    balance_url: str | None = None
    # 圆桌自己也用同一个 key 的渠道（models.yaml 里的渠道名）：这部分花费从余额差值里扣掉
    shared_channels: list[str] = Field(default_factory=list)
    monthly_usd: float = Field(default=3.0, ge=0)  # 工作台每月花费上限（UTC 自然月，软上限）
    balance_check_minutes: float = Field(default=5, ge=1, le=120)
    # 模型服务扣费有延迟（2026-11-04 查账：10-30、10-31 三次生成估算约 ¥20～39，账本只记到约 ¥7）：
    # dsh 停下或活干完以后这么多分钟内，余额继续往下掉仍算工作台的（扣掉共用渠道），
    # 下一件活开始时也不重置起点，接着记
    settle_minutes: float = Field(default=180, ge=0, le=1440)
    # 和哪个渠道（models.yaml）共用一份每周上限（weekly_cap_usd）：dsh 本周花的算进那个渠道，
    # 合计到了上限 dsh 停到下周。为空或渠道没设每周上限时不管
    cap_channel: str | None = None
    # 余额币种换算成美元：{币种: 1 美元合多少}，美元本身不用写
    usd_rates: dict[str, float] = Field(default_factory=lambda: {"CNY": 7.2})
    # 页面上花费、上限、余额用什么币种显示（USD 或 usd_rates 里有的）；上限仍按 monthly_usd 算
    display_currency: str = "CNY"
    # dsh 的文件权限：read-only 只读（默认）/ workspace-write 只能写工作区 / danger-full-access
    permission_mode: Literal["read-only", "workspace-write", "danger-full-access"] = "read-only"
    # 超级电脑派给 dsh 的活：只能写这件活的共享工作文件夹（dsh 的工作区就是当前目录）
    job_permission_mode: Literal["read-only", "workspace-write", "danger-full-access"] = (
        "workspace-write"
    )
    # 每件活的步数和上下文（2026-11-04 用户回「做」，docs/DSH.md「每件活的步数和上下文」）：
    # 调一次工具算一步，超过 job_max_steps 先停，再接着同一段对话用最多 wrapup_steps 步收尾
    # （提示词 dsh_wrapup）。0 = 不限
    job_max_steps: int = Field(default=60, ge=0, le=1000)
    wrapup_steps: int = Field(default=3, ge=0, le=20)
    # 上下文涨到模型窗口的这个比例就把旧的历史压成摘要（dsh 自己默认 0.8），近期的原样留这么多
    job_context_ratio: float = Field(default=0.25, gt=0, le=0.9)
    job_retain_ratio: float = Field(default=0.05, gt=0, lt=0.9)
    # 工具输出超过这么多字只留开头和结尾（dsh 自己默认 8192）
    job_tool_chars: int = Field(default=4096, ge=1024, le=65536)
    # 错峰：DeepSeek 2026-08 起高峰时段贵一倍（工作日 UTC 01:00–04:00、06:00–10:00，周末全天闲时）。
    # avoid_peak 打开时高峰时段不派活给 dsh（包月的照常），过了高峰再补位
    avoid_peak: bool = False  # 仓库配置里打开；默认关，测试不随真实时间变
    peak_utc: list[str] = Field(default_factory=lambda: ["01:00-04:00", "06:00-10:00"])

    @field_validator("peak_utc")
    @classmethod
    def _peak_ranges(cls, v: list[str]) -> list[str]:
        for item in v:
            if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-4]):[0-5]\d", item):
                raise ValueError(f"peak_utc 要写成 HH:MM-HH:MM（UTC），{item!r} 不对")
        return v

    def in_peak(self, now: datetime) -> bool:
        """现在（UTC）是不是 DeepSeek 的高峰时段：只算工作日。"""
        now = now.astimezone(UTC)
        if not self.avoid_peak or now.weekday() >= 5:
            return False
        minute = now.hour * 60 + now.minute
        for item in self.peak_utc:
            start, end = item.split("-")
            a = int(start[:2]) * 60 + int(start[3:])
            b = int(end[:2]) * 60 + int(end[3:])
            if a <= minute < b:
                return True
        return False

    @field_validator("key_env")
    @classmethod
    def _env_name(cls, v: str | None) -> str | None:
        if v is not None and not _ENV_NAME.match(v):
            raise ValueError("key_env 只能写环境变量名（大写字母、数字、下划线），不能写 key 本身")
        return v

    @field_validator("display_currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _known_display(self) -> DshRules:
        if self.job_retain_ratio >= self.job_context_ratio:
            raise ValueError(
                "job_retain_ratio 要小于 job_context_ratio（留下的不能比压缩的门槛还多）"
            )
        cur = self.display_currency
        if cur != "USD" and cur not in {k.upper() for k in self.usd_rates}:
            raise ValueError(f"display_currency {cur} 没有汇率：在 usd_rates 里加上它")
        return self

    def display_rate(self) -> float:
        """1 美元合多少显示币种。"""
        rates = {k.upper(): v for k, v in self.usd_rates.items()}
        return 1.0 if self.display_currency == "USD" else rates[self.display_currency]


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
    solo_pipeline: list[str] = Field(default_factory=lambda: ["answer", "reveal"], min_length=1)
    brainstorm_pipeline: list[str] = Field(
        default_factory=lambda: ["ideate", "crossbreed", "vote", "organize", "reveal"],
        min_length=1,
    )
    # 办公室模式（R17）：经理分诊 → 员工办理 → 经理过目
    office_pipeline: list[str] = Field(
        default_factory=lambda: ["intake", "handle", "check", "reveal"], min_length=1
    )
    brainstorm: BrainstormRules = BrainstormRules()
    collab: CollabRules = CollabRules()
    channel_mode: ChannelMode = "auto"
    channel_priority: ChannelPriority = ChannelPriority()
    request: RequestPolicy = RequestPolicy()
    step_params: dict[str, dict[str, Any]] = Field(default_factory=dict)
    length_guard: LengthGuard = LengthGuard()
    review_quality: ReviewQuality = ReviewQuality()
    effort_check: EffortCheck = EffortCheck()
    uploads: UploadRules = UploadRules()
    tools: ToolsConfig = ToolsConfig()
    media: MediaRules = MediaRules()
    limits: Limits = Limits()
    display: DisplayConfig = DisplayConfig()
    style: StyleRules = StyleRules()
    office: OfficeRules = OfficeRules()
    chat: ChatRules = ChatRules()
    plain_talk: PlainTalkRules = PlainTalkRules()
    evolution: EvolutionRules = EvolutionRules()
    mail: MailConfig = MailConfig()
    clipper: ClipperRules = ClipperRules()
    perception: PerceptionRules = PerceptionRules()
    dsh: DshRules = DshRules()
    supercomputer: SupercomputerRules = SupercomputerRules()
    bench: BenchRules = BenchRules()
    calendar: CalendarRules = CalendarRules()
    backup: BackupRules = BackupRules()
    gamemode: GameModeRules = GameModeRules()

    @field_validator("prompts")
    @classmethod
    def _versions(cls, prompts: dict[str, str]) -> dict[str, str]:
        bad = {k: v for k, v in prompts.items() if not _PROMPT_VERSION.match(v)}
        if bad:
            raise ValueError(f"提示词版本必须形如 v1、v2：{bad}")
        return prompts

    @field_validator(
        "pipeline", "collab_pipeline", "solo_pipeline", "brainstorm_pipeline", "office_pipeline"
    )
    @classmethod
    def _unique_steps(cls, steps: list[str]) -> list[str]:
        if len(steps) != len(set(steps)):
            raise ValueError(f"pipeline 中的步骤不能重复：{steps}")
        return steps

    @model_validator(mode="after")
    def _bench_kinds(self) -> RoundtableConfig:
        known = set(self.supercomputer.kinds) | {"split", "merge"}
        b = self.bench
        bad = [k for k in (b.plan_kind, b.window_kind, b.review_kind, b.ask_kind) if k not in known]
        if bad:
            raise ValueError(f"bench 用到的活的种类不在 supercomputer.kinds 里：{bad}")
        return self

    @model_validator(mode="after")
    def _members(self) -> RoundtableConfig:
        if self.min_members > self.seats:
            raise ValueError("min_members 不能大于 seats")
        return self

    @model_validator(mode="after")
    def _plain_talk_roles(self) -> RoundtableConfig:
        if not self.plain_talk.enabled:
            return self
        needed = {"plain_talk"} | ({"plain_rewrite"} if self.plain_talk.check.enabled else set())
        missing = sorted((needed | set(self.plain_talk.roles)) - set(self.prompts))
        if missing:
            raise ValueError(f"plain_talk 用到的提示词没有在 prompts 里配置版本：{missing}")
        return self


# --- personas.yaml -------------------------------------------------------------


class Persona(_Strict):
    """聊天里的人设（只用于私聊、群聊、朋友圈；工作模式与办公室的提示词不读取）。

    全部可空：为空时用模型本身的说话风格。网页「成员设定」里改的存在本地数据库，优先于这里。
    """

    personality: str = Field(default="", max_length=300)  # 性格
    speaking_style: str = Field(default="", max_length=300)  # 说话风格
    catchphrases: list[str] = Field(default_factory=list, max_length=5)  # 口头禅
    interests: list[str] = Field(default_factory=list, max_length=8)  # 兴趣（上网看什么、聊什么）
    voice: str = Field(default="", max_length=60)  # 语音音色（预留）


class Signature(_Strict):
    """一位成员最有辨识度的说话特色（只用于私聊、群聊、生活、朋友圈；2026-10-31 用户定）。

    写进说话习惯段的第一行（不会因为字数被挤掉）；句尾小尾巴发出前按概率补上；
    招牌词和小尾巴不会被「口癖慢慢变」换掉或歇掉。"""

    line: str = Field(default="", max_length=160)  # 写进说话习惯段第一行
    tails: list[str] = Field(default_factory=list, max_length=3)  # 句尾小尾巴，如「喵」
    tail_chance: float = Field(default=0.0, ge=0, le=1)  # 一条消息带小尾巴的概率（带也只带一个）
    words: list[str] = Field(default_factory=list, max_length=6)  # 招牌词（口癖漂移不动）
    self_names: list[str] = Field(default_factory=list, max_length=3)  # 招牌自称，如「本小姐」
    self_chance: float = Field(
        default=1.0, ge=0, le=1
    )  # 一条里可以用招牌自称的概率，没中换成「我」

    @field_validator("tails", "words", "self_names")
    @classmethod
    def _short(cls, items: list[str]) -> list[str]:
        if any(not w.strip() or len(w) > 8 for w in items):
            raise ValueError("小尾巴和招牌词每个 1～8 字")
        return items


class StickerSpec(_Strict):
    name: str = Field(min_length=1, max_length=12)  # 表情名（聊天里写 [表情:包名·表情名]）
    caption: str = Field(min_length=1, max_length=16)  # 画进图里的配文
    action: str = Field(min_length=1, max_length=120)  # 动作和表情
    # 动图：四格画面里依次的动作（正好 4 条）；不写就是一张静态贴纸
    frames: list[str] = Field(default_factory=list, max_length=4)
    # 什么时候发：写进给成员的表情包清单（只有名字时模型会望文生义，如把记仇的「记着」当「好的」）
    use: str = Field(default="", max_length=24)
    # 这张图的意思等于哪些话：一句话里已经有这些词时再配这张就重复了（发出前去重用）；不影响画图
    means: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("means")
    @classmethod
    def _short(cls, words: list[str]) -> list[str]:
        if any(not w.strip() or len(w) > 8 for w in words):
            raise ValueError("means 每个词 1～8 字")
        return words

    @field_validator("frames")
    @classmethod
    def _four(cls, frames: list[str]) -> list[str]:
        if frames and (len(frames) != 4 or any(not f.strip() or len(f) > 60 for f in frames)):
            raise ValueError("动图要正好 4 格，每格的动作 1～60 字")
        return frames


class PhotoHabit(_Strict):
    """一位成员自己拍照时的习惯（只影响权重，不排除任何选项；只用于朋友圈配图，工作模式不读）。
    subjects：爱拍的东西（英文短语），拍东西时按概率换成其中一样；boost：对通用维度表的权重倍数
    （维度 → {表项 name: 倍数}，维度是 shots / angles / lights / moments / lenses / filters）；
    quirk：一句英文小毛病，她自己拍时按概率出现。"""

    subjects: list[str] = Field(default_factory=list, max_length=8)
    boost: dict[
        Literal["shots", "cameras", "angles", "lights", "moments", "lenses", "filters", "styles"],
        dict[str, float],
    ] = Field(default_factory=dict)
    quirk: str = Field(default="", max_length=200)
    # 她想不想把房东（用户）拉进画面（看她的性格）：乘在 media.moment_photos.landlord_chance 上
    landlord: float = Field(default=1.0, ge=0, le=5)

    @field_validator("boost")
    @classmethod
    def _positive(cls, boost: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        for dim, table in boost.items():
            bad = sorted(k for k, v in table.items() if v <= 0)
            if bad:
                raise ValueError(f"boost.{dim} 的倍数必须大于 0：{bad}")
        return boost


class StickerCard(_Strict):
    """一位成员的形象卡 + 专属表情清单（只用于聊天、朋友圈；工作模式不读）。"""

    look: str = Field(min_length=1, max_length=400)  # 外观要点
    # 朋友圈配图用：她自己的五官（眼睛颜色和形状、表情习惯、痣、虎牙……），接在 look 后面写进提示词，
    # 免得几位画成同一张脸（11-02 用户看对比图：角色面部有些雷同）
    face: str = Field(default="", max_length=300)
    must: list[str] = Field(default_factory=list, max_length=8)  # 每张都必须画到的细节
    # 外观的英文标签（逗号分隔，给吃标签的本地绘图模型用，见 moment_photo_tags）；不写时用 look
    look_tags: str = Field(default="", max_length=400)
    # 形象参考图：素材目录（members.json 所在目录）里的相对路径，最多 3 张；
    # 不写时用 members.json 里的立绘 / 小人 / 头像
    refs: list[str] = Field(default_factory=list, max_length=3)
    stickers: list[StickerSpec] = Field(default_factory=list, max_length=24)
    # 朋友圈配图用：标志小物件（只露手、背影时也认得出是谁，如小鲸鱼发卡）
    cues: list[str] = Field(default_factory=list, max_length=4)
    # 朋友圈配图：她自己拍照时的习惯（爱拍什么、爱用的机位 / 焦段 / 滤镜、小毛病）；不写时用通用权重
    photo: PhotoHabit | None = None
    # 朋友圈日常照必须画到的细节；不写时用 must（日常照不扛武器等大道具时在这里去掉）
    daily_must: list[str] | None = Field(default=None, max_length=8)

    @field_validator("stickers")
    @classmethod
    def _unique(cls, items: list[StickerSpec]) -> list[StickerSpec]:
        names = [s.name for s in items]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"表情名重复：{dup}")
        return items


class PersonasConfig(_Strict):
    # 代号前缀；留空时代号本身就是称呼（塔罗牌名、昵称）
    code_prefix: str = "组员"
    # 匿名开启时使用的代号池（塔罗牌）：随机分配，不含任何身份信息
    codes: list[str] = Field(min_length=2)
    # 匿名关闭时的称呼：厂商 → 昵称，档位 → 模式名；"昵称·模式"，统筹再加后缀
    nicknames: dict[str, str] = Field(default_factory=dict)
    tier_labels: dict[Tier, str] = Field(default_factory=dict)
    nickname_separator: str = "·"
    coordinator_suffix: str = "（统筹）"
    manager_suffix: str = "（经理）"  # 办公室模式里统筹就是经理
    personas: dict[str, Persona] = Field(default_factory=dict)  # 厂商 → 人设
    # 厂商 → 最有辨识度的说话特色（优先于人物卡里没写够的地方；只用于聊天和生活）
    signatures: dict[str, Signature] = Field(default_factory=dict)
    # 成员固定音色（合成音色，不克隆真人）：厂商 → {语音模型 id → 该模型的音色名}
    voices: dict[str, dict[str, str]] = Field(default_factory=dict)
    # 成员专属表情包：厂商 → 形象卡与表情清单
    sticker_cards: dict[str, StickerCard] = Field(default_factory=dict)
    # 成员生日（日历用，只用于生活）：厂商 → MM-DD；默认取各自模型首次公开发布的日子，可改
    birthdays: dict[str, str] = Field(default_factory=dict)

    @field_validator("birthdays")
    @classmethod
    def _birthdays(cls, days: dict[str, str]) -> dict[str, str]:
        from datetime import date

        for who, md in days.items():
            try:
                date.fromisoformat(f"2000-{md}")  # 2000 年是闰年，02-29 也算合法
            except ValueError:
                raise ValueError(f"{who} 的生日要写成 MM-DD：{md}") from None
        return days

    @field_validator("codes")
    @classmethod
    def _unique_codes(cls, codes: list[str]) -> list[str]:
        if len(codes) != len(set(codes)):
            raise ValueError("代号不能重复")
        return codes

    @model_validator(mode="after")
    def _distinct_names(self) -> PersonasConfig:
        names = list(self.nicknames.values())
        if len(names) != len(set(names)):
            raise ValueError("nicknames 中的昵称不能重复")
        clash = sorted(set(names) & set(self.codes))
        if clash:
            raise ValueError(f"昵称不能与匿名代号相同：{clash}")
        return self


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


class PanelSpec(_Strict):
    """分诊小组：题目开始时几位最便宜的模型各给一条作答方式建议（只推荐，用户确认）。"""

    size: int = Field(default=3, ge=1, le=7)
    tiers: list[Tier] = Field(default_factory=lambda: ["budget"], min_length=1)
    max_tokens: int = Field(default=200, gt=0)
    expected_input_tokens: int = Field(default=500, gt=0)
    expected_output_tokens: int = Field(default=100, gt=0)
    # 规则已能判断为简单题时不调用小组，直接建议个人作答
    skip_when_simple: bool = True
    # 小组没有给出有效建议时的作答方式
    default_workflow: Literal["solo", "discussion", "collab"] = "discussion"
    # 难度（取中位数）对应推荐的档位
    tier_by_difficulty: dict[Difficulty, str] = Field(
        default_factory=lambda: {"simple": "budget", "medium": "budget", "hard": "flagship"}
    )

    @model_validator(mode="after")
    def _all_difficulties(self) -> PanelSpec:
        missing = {"simple", "medium", "hard"} - set(self.tier_by_difficulty)
        if missing:
            raise ValueError(f"tier_by_difficulty 缺少难度 {sorted(missing)}")
        return self


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
    discuss_tokens: int = Field(default=250, ge=0)
    accept_tokens: int = Field(default=500, ge=0)
    merge_tokens: int = Field(default=3000, ge=0)
    ideate_tokens: int = Field(default=500, ge=0)
    expand_tokens: int = Field(default=400, ge=0)
    crossbreed_tokens: int = Field(default=450, ge=0)
    vote_tokens: int = Field(default=300, ge=0)
    organize_tokens: int = Field(default=1500, ge=0)
    # 办公室模式：经理分诊（JSON + 工作说明）、经理过目（JSON）的输出长度；每位员工在名单里占的长度
    intake_tokens: int = Field(default=400, ge=0)
    check_tokens: int = Field(default=300, ge=0)
    roster_tokens_per_staff: int = Field(default=40, ge=0)
    # 开会（O3）：每位员工发言、经理拍板（结论 + 纪要）的输出长度
    speech_tokens: int = Field(default=600, ge=0)
    minutes_tokens: int = Field(default=1500, ge=0)
    # 没有历史记录时，输出 token 按档位放大（推理模型的思考 token 也按输出计费）
    output_multiplier: dict[str, float] = Field(default_factory=dict)
    # 某个模型在某个步骤至少有这么多次成功调用，才用它的历史中位数代替公式
    history_min_samples: int = Field(default=3, ge=1)
    # 统计历史时只看最近这么多场讨论
    history_sessions: int = Field(default=30, ge=1)
    # 能用工具的步骤，每个座位平均多几次模型调用（工具轮次）；有历史记录后改用实际次数
    tool_rounds: float = Field(default=0.5, ge=0)
    # 能搜索的步骤，每个座位平均搜索几次、读取几个网页（按搜索服务单价计入预估）
    searches: float = Field(default=1.0, ge=0)
    fetches: float = Field(default=0.5, ge=0)
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


class MultiMediaRule(_Strict):
    """讨论模式下题目需要生成多个媒体文件的检测（规则判断，不调用模型）。

    讨论模式让每位成员各自把整题做一遍，要产出多张图 / 多段媒体时每人都会各做一整套，花费成倍增加；
    检测到时在提交前建议改用协同模式（分工，每人只做一部分）。
    """

    enabled: bool = True
    min_count: int = Field(default=2, ge=2)  # 数量 ≥ 此值才算"多个"
    # 媒体名词（数量词后面紧跟的东西）
    nouns: list[str] = Field(min_length=1)
    # 数量词与量词：1–10 的中文数字和"几 / 多 / 若干"自动识别；量词在这里配置
    classifiers: list[str] = Field(default_factory=list)
    # 明确表示"多个 / 一套 / 拼接"的词，且题目里出现媒体名词时也算
    hints: list[str] = Field(default_factory=list)


class RoutingConfig(_Strict):
    confirm_threshold_usd: float = Field(default=0.30, ge=0)
    default_plan: str
    default_difficulty: Difficulty = "medium"
    planner: PlannerSpec = PlannerSpec()
    panel: PanelSpec = PanelSpec()
    triage: list[TriageRule] = Field(default_factory=list)
    multi_media: MultiMediaRule | None = None
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
        stray = sorted(set(rt.channel_priority.order) - set(self.models.channels))
        if stray:
            raise ValueError(f"channel_priority.order 中有未知渠道：{stray}")
        unknown_channels = sorted(set(rt.dsh.shared_channels) - set(self.models.channels))
        if unknown_channels:
            raise ValueError(f"dsh.shared_channels 里有未知渠道：{unknown_channels}")
        # 音色只能配给语音模型；名单里没有的 id 不报错（模型下架或换了名单时音色表留着也无害）
        not_tts = {m.id for m in self.models.models if "tts" not in m.tags}
        for member, voices in personas.voices.items():
            bad = sorted(set(voices) & not_tts)
            if bad:
                raise ValueError(f"voices.{member} 里有不是语音模型（tts 标签）的 id：{bad}")
        self._check_routing()
        self._check_collab()
        self._check_photo_habits()
        return self

    def _check_photo_habits(self) -> None:
        """拍照习惯的 boost 只能引用朋友圈配图维度表里有的名字。"""
        rules = self.roundtable.media.moment_photos
        for member, card in self.personas.sticker_cards.items():
            if card.photo is None:
                continue
            for dim, table in card.photo.boost.items():
                unknown = sorted(set(table) - rules.names(dim))
                if unknown:
                    raise ValueError(
                        f"sticker_cards.{member}.photo.boost.{dim} 引用了不存在的名字：{unknown}"
                        f"（见 media.moment_photos.{dim}）"
                    )

    def _check_collab(self) -> None:
        rt = self.roundtable
        vocab = set(self.models.tag_vocabulary)
        for name, kind in rt.collab.kinds.items():
            if kind.prompt not in rt.prompts:
                raise ValueError(
                    f"collab.kinds.{name}.prompt 引用了 prompts 中没有的角色 {kind.prompt!r}"
                )
            unknown = sorted(set(kind.tags) - vocab)
            if unknown:
                raise ValueError(f"collab.kinds.{name}.tags 含未知标签 {unknown}")
        for template in rt.collab.templates:
            for step in template.steps:
                if step.media not in (None, "image", "speech", "video", "music"):
                    raise ValueError(f"模板 {template.name} 的步骤 {step.id} 的 media 无效")

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
        if "panel" not in rt.prompts:
            raise ValueError("roundtable.yaml 的 prompts 缺少 panel 版本")
        unknown_tiers = sorted(set(routing.panel.tier_by_difficulty.values()) - set(routing.plans))
        if unknown_tiers:
            raise ValueError(f"routing.panel.tier_by_difficulty 引用了不存在的档位 {unknown_tiers}")
        for name, plan in routing.plans.items():
            missing = sorted(set(plan.prompt_roles.values()) - set(rt.prompts))
            if missing:
                raise ValueError(
                    f"档位 {name} 的 prompt_roles 引用了 prompts 中没有的角色 {missing}"
                )


# 搬到 sections/ 的配置模型照旧可以从这里导入
__all__ = [
    "AboutRules",
    "AcceptanceRules",
    "AmbivalenceRules",
    "AppConfig",
    "AskedRules",
    "AssocRules",
    "AttachStyle",
    "BackupRules",
    "BaselineRules",
    "BenchApps",
    "BenchDesk",
    "BenchDeskCategory",
    "BenchDeskFeed",
    "BenchHero",
    "BenchKind",
    "BenchMaterials",
    "BenchNetService",
    "BenchProject",
    "BenchResearch",
    "BenchRules",
    "BenchSkillStep",
    "BenchSkills",
    "BenchTeam",
    "BodyRhythm",
    "BrainRules",
    "BrainstormRules",
    "BrowseRules",
    "Budget",
    "BusRules",
    "CadenceRules",
    "CalendarObservance",
    "CalendarRegion",
    "CalendarRules",
    "CardRules",
    "CareRules",
    "ChannelKind",
    "ChannelMode",
    "ChannelPriority",
    "ChannelSpec",
    "ChatActionRules",
    "ChatBudgetRules",
    "ChatRules",
    "CircadianRules",
    "CircleRules",
    "ClarifyRules",
    "ClassicMeme",
    "ClipperRules",
    "CollabRules",
    "ConfabRules",
    "Confidence",
    "ContextRules",
    "CoordinatorRule",
    "CrossReviewRules",
    "CustomSpec",
    "DIFFICULTIES",
    "DiaryToolRules",
    "Difficulty",
    "DiscussionRules",
    "DisplayConfig",
    "DriftRules",
    "DriveRule",
    "DshRules",
    "EffortCheck",
    "EmojiImageRules",
    "EmpathyRules",
    "EngineRules",
    "EpisodeRules",
    "EscalationRule",
    "EstimateParams",
    "EvalRules",
    "EvolutionImplementRules",
    "EvolutionPluginRules",
    "EvolutionRules",
    "EvolutionSchedule",
    "EvolutionScoutRules",
    "FeedSpec",
    "FeelRules",
    "FestivalRules",
    "FestivalSpec",
    "FileRules",
    "FirstsRules",
    "FlawRule",
    "FloorRules",
    "GameModeRules",
    "GistRule",
    "GoalRules",
    "GrowthBookRules",
    "GrowthHabitRules",
    "GrowthPlasticRules",
    "GrowthReportRules",
    "GrowthReviewRules",
    "GrowthRules",
    "GrowthVitalsRules",
    "HeartbeatRules",
    "HerInPhoto",
    "HooksRules",
    "HumanRules",
    "HushRules",
    "ImageApi",
    "ImageTool",
    "InsightDepth",
    "InsightRules",
    "IntentRule",
    "KindRule",
    "LearnRules",
    "LengthGuard",
    "Lens",
    "LifeGuardRules",
    "LifeRules",
    "LifeWayRules",
    "Limits",
    "LingoRules",
    "LoveRules",
    "LoveStyle",
    "LowRules",
    "LunarFestival",
    "MailAIRules",
    "MailConfig",
    "MediaPrice",
    "MediaRules",
    "MediaUnit",
    "MemeRules",
    "MemeSource",
    "MemoryLinkRules",
    "MemoryRules",
    "MergeTrimRules",
    "MetaRules",
    "ModelSpec",
    "ModelsConfig",
    "MomentForm",
    "MomentPhotoRules",
    "MomentShot",
    "MomentsRules",
    "MultiMediaRule",
    "MusicRules",
    "NickRules",
    "OfficeMeeting",
    "OfficePerformance",
    "OfficeProject",
    "OfficeRole",
    "OfficeRules",
    "OfficeTaskCaps",
    "OpenerRules",
    "PHOTO_DIMS",
    "PHOTO_PLACES",
    "PHOTO_TIMES",
    "PaceRules",
    "PanelSpec",
    "PerceptionListen",
    "PerceptionMode",
    "PerceptionRules",
    "Persona",
    "PersonasConfig",
    "PhaseKind",
    "PhaseRules",
    "PhotoHabit",
    "PhotoOption",
    "PhraseEntry",
    "PhraseLearnRules",
    "PhrasebookRules",
    "PipelineRules",
    "PipelineTemplate",
    "PlainTalkCheck",
    "PlainTalkRule",
    "PlainTalkRules",
    "PlanSpec",
    "PlannerSpec",
    "PoleRule",
    "Price",
    "Privilege",
    "ProfileRules",
    "ProgressKind",
    "ProgressRules",
    "PsycheRules",
    "PythonTool",
    "ReachRules",
    "ReferenceRules",
    "ReflexRule",
    "RelationRules",
    "RelationStage",
    "RelationWeights",
    "RequestPolicy",
    "ReviewQuality",
    "RewardRules",
    "RhythmRules",
    "RiceRules",
    "RoundtableConfig",
    "Route",
    "RoutineRules",
    "RoutingConfig",
    "RuleAction",
    "RuleCondition",
    "SKILL_STEPS",
    "SchedulerRules",
    "SchedulerShares",
    "SearchPrice",
    "SearchProviderSpec",
    "SearchTool",
    "ShowRule",
    "ShowRules",
    "Signature",
    "SlopRules",
    "SpeechRules",
    "StashFollowRules",
    "StashRules",
    "StateLayerRules",
    "StateRules",
    "StickerCard",
    "StickerPackRules",
    "StickerRules",
    "StickerSpec",
    "StoryRules",
    "StorySpec",
    "StoryStep",
    "Strategy",
    "StyleRules",
    "SuperButler",
    "SuperGate",
    "SuperJsonOutput",
    "SuperWeights",
    "SuperWorker",
    "SupercomputerRules",
    "SwayRules",
    "TalentDomain",
    "TalentRules",
    "TalentTexts",
    "TemplateDep",
    "TemplateStep",
    "TemplateWhen",
    "TensionRule",
    "Tier",
    "TokenBounds",
    "ToolName",
    "ToolsConfig",
    "TraitPick",
    "TravelDest",
    "TravelRules",
    "TravelTexts",
    "TriageRule",
    "TypoRules",
    "UploadRules",
    "ValueAxis",
    "ValueRules",
    "VideoRules",
    "VoiceCheckRules",
    "VoiceMode",
    "VoiceRules",
    "WanderRules",
    "WanderSite",
    "WatchRules",
    "WhimRules",
    "_DATE_RULE",
    "_Strict",
    "_evening_hours",
]
