"""配置文件的 pydantic schema。只描述与校验数据，不读文件、不读环境变量。"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ChannelKind = Literal["aggregator", "direct", "local"]
ChannelMode = Literal["openrouter", "direct", "auto"]
# 档位：flagship 旗舰（高价高能力）/ budget 便宜档。成本优先路由只看档位和能力标签
Tier = Literal["flagship", "budget"]
ImageApi = Literal["chat", "images"]
OfficeRole = Literal["manager", "staff"]

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
    # none = 不发送（渠道不支持时忽略，不报错）。不填：聚合平台 object，其余 none
    reasoning: Literal["object", "effort", "none"] | None = None
    # 账户余额查询（只读，供「余额与用量」面板）：credits = GET {base_url}/credits（账户充值与已用）
    # 加 GET {base_url}/key（这个 key 的额度与今天 / 本周 / 本月已用）。不填表示该渠道不查余额
    balance: Literal["credits"] | None = None
    # 每月赠送 / 包含的额度（美元，如会员附送的 API 额度）。渠道优先顺序据此判断：
    # 本月按本机账本还没用完时排在前面，用完后排到最后。可在界面里改（存 app_settings）
    monthly_credit_usd: float | None = Field(default=None, ge=0)

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

    @model_validator(mode="before")
    @classmethod
    def _apply_switches(cls, data: Any) -> Any:
        if not isinstance(data, dict) or not data.get("model_switches"):
            return data
        switches = data["model_switches"]
        if not isinstance(switches, dict):
            return data  # 交给字段校验报错
        models = data.get("models") or []
        known = {m.get("id") for m in models if isinstance(m, dict)}
        unknown = sorted(str(k) for k in switches if k not in known)
        if unknown:
            raise ValueError(f"model_switches 里有不存在的模型：{unknown}")
        return {
            **data,
            "models": [
                {**m, "enabled": switches[m["id"]]}
                if isinstance(m, dict) and m.get("id") in switches
                else m
                for m in models
            ],
        }

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


class MomentShot(_Strict):
    """朋友圈配图的一种拍法。her：none 她不入镜（不传形象参考图）/ part 只露手、袖子、
    尾巴 / full 入镜。"""

    text: str = Field(min_length=1)
    weight: float = Field(default=1.0, gt=0)
    her: Literal["none", "part", "full"] = "full"
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
                text="a friend caught her in the middle of doing it, candid, "
                "she is the main subject",
                weight=4,
            ),
            MomentShot(
                text="a casual selfie at arm's length, maybe with whoever she is with",
                weight=2,
            ),
            MomentShot(
                text="her and the people she is with, snapped together while hanging out",
                weight=1,
            ),
            MomentShot(
                text="something worth showing off in front of her (a nice meal just served, "
                "the view, a cute object), with her hand or sleeve at the edge of the frame",
                weight=2,
                her="part",
            ),
            MomentShot(
                text="only a view or thing worth showing off (rooftop night sky, a pretty dish "
                "just served); she does not appear",
                weight=1,
                her="none",
            ),
        ],
        min_length=1,
        max_length=8,
    )
    phone_look: bool = True  # 画完在本机加一层手机味：轻微裁歪、压暗饱和度、噪点、压缩（不花钱）
    room_refs: bool = True  # 按事件地点附上那间房的场景图当最后一张参考图（公寓里拍）


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


class OfficeTaskCaps(_Strict):
    """办公室模式：每件事的工资上限（美元），按办事方式分别设；预计超出时先问老板。"""

    single: float = Field(default=0.05, gt=0)  # 单人办
    meeting: float = Field(default=0.15, gt=0)  # 开会
    project: float = Field(default=0.80, gt=0)  # 项目分工


class OfficeMeeting(_Strict):
    """办公室模式的开会（O3）：经理点名几位员工依次发言，再拍板写纪要。"""

    min_attendees: int = Field(default=2, ge=2)
    max_attendees: int = Field(default=3, ge=2)

    @model_validator(mode="after")
    def _range(self) -> OfficeMeeting:
        if self.max_attendees < self.min_attendees:
            raise ValueError("max_attendees 不能小于 min_attendees")
        return self


class OfficeProject(_Strict):
    """办公室模式的项目分工（O4）：经理点名一个项目组，沿用协同流水线，但只派需要的人。

    pipeline：项目组那张桌子的步骤（协同流水线 + 复核经理验收 office_review）；
    reviews_per_item：交叉审查时每份成果几位审查者（只审关键，默认 1）；
    step_params：项目组桌子上覆盖的步骤参数（如合并由主办经理以中等思考强度完成），
    同一步骤所有人相同。
    """

    min_members: int = Field(default=2, ge=2)
    max_members: int = Field(default=4, ge=2)
    pipeline: list[str] = Field(
        default_factory=lambda: [
            "decompose",
            "volunteer",
            "assign",
            "work",
            "cross_review",
            "rework",
            "accept",
            "merge",
            "office_review",
            "reveal",
        ],
        min_length=1,
    )
    reviews_per_item: int = Field(default=1, ge=1)
    step_params: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _range(self) -> OfficeProject:
        if self.max_members < self.min_members:
            raise ValueError("max_members 不能小于 min_members")
        if len(self.pipeline) != len(set(self.pipeline)):
            raise ValueError(f"pipeline 中的步骤不能重复：{self.pipeline}")
        return self


class OfficePerformance(_Strict):
    """办公室模式的绩效（O5）：名单附绩效给经理参考；规则派活时工资相同的优先通过率高的。"""

    enabled: bool = True
    min_jobs: int = Field(default=3, ge=1)


class OfficeRules(_Strict):
    """办公室模式（O1～O5，见 docs/OFFICE.md）。经理在 models.yaml 的 office_role 里指定。"""

    task_caps_usd: OfficeTaskCaps = OfficeTaskCaps()
    meeting: OfficeMeeting = OfficeMeeting()
    project: OfficeProject = OfficeProject()
    performance: OfficePerformance = OfficePerformance()
    # 两位以上经理时怎么分：rotate = 每件事轮流当主办经理，项目级任务由另一位复核
    manager_strategy: Literal["rotate"] = "rotate"


class ChatBudgetRules(_Strict):
    """聊天这一块（私聊、群聊，以后的生活与上网）每月最多花多少（UTC 自然月）；也计入总预算。"""

    monthly_usd: float = Field(default=5.0, gt=0)


class MemoryLinkRules(_Strict):
    """联想网（P2，M22 档 1 的记忆部分）：一起出现 / 一起被想起的记忆连得更紧（赫布学习），
    想起一件时顺着连接想到别的（扩散激活），夜里顺着连接随便走几步（随机游走）。程序算，不调用模型。"""

    enabled: bool = True
    learn_rate: float = Field(default=0.2, gt=0, le=1)  # 每次一起出现，强度向 1 靠近的比例
    nightly_decay: float = Field(default=0.02, ge=0, lt=1)  # 每晚所有连接衰减的比例
    min_weight: float = Field(default=0.05, ge=0, lt=1)  # 低于它的连接删掉
    max_links: int = Field(default=20, ge=1, le=200)  # 每件记忆最多留几条最强的连接
    hops: int = Field(default=2, ge=1, le=3)  # 扩散几步
    spread: float = Field(default=0.5, gt=0, le=1)  # 每走一步激活乘以 强度 × spread
    seeds: int = Field(default=4, ge=1, le=20)  # 从分数最高的几件开始扩散
    assoc_max: int = Field(default=1, ge=0, le=5)  # 每次最多多带几件「联想到的」
    walks: int = Field(default=2, ge=0, le=10)  # 每晚随机游走几次
    walk_steps: int = Field(default=3, ge=1, le=20)  # 每次走几步


class DiaryToolRules(_Strict):
    """翻日记（P2）：生活模式里成员自己翻以前的日记和记忆，每位成员每天有次数上限（所有成员相同）。"""

    enabled: bool = True
    daily_reads: int = Field(default=3, ge=0, le=20)  # 每位成员每天最多翻几次（本地日）
    max_hits: int = Field(default=3, ge=1, le=10)  # 每次最多翻出几段
    result_chars: int = Field(default=300, ge=50, le=2000)  # 每次翻出的内容最多多少字


class MemoryRules(_Strict):
    """成员的记忆（docs/CHAT.md G3、docs/MEMBER_ENGINE.md「P2 记忆与联想」）。

    只存本地；只用于生活模式。
    """

    enabled: bool = True
    context_chars: int = Field(default=800, ge=0)  # 聊天提示词里最多放多少字的记忆（所有成员相同）
    # 私聊时接上大家庭群里最近 group_hours 小时的最多 group_lines 句（每句最多 group_chars 字）
    group_lines: int = Field(default=6, ge=0, le=20)
    group_hours: float = Field(default=12, gt=0, le=48)
    group_chars: int = Field(default=60, ge=10, le=200)
    entry_chars: int = Field(default=300, ge=50)  # 每条记忆最多多少字
    log_chars: int = Field(default=6000, ge=500)  # 整理一天时最多读多少字的聊天记录
    catch_up_days: int = Field(default=7, ge=1, le=60)  # 最多补整理几天前的
    # 夜里整理是一段 JSON，思考也算在输出里：600 时三位一个字都没写出来（10-31 真模型试跑）
    max_tokens: int = Field(default=2500, ge=64)
    estimate_usd: float = Field(default=0.003, ge=0)  # 每次整理的预估花费（预算检查用）
    # 召回方式：half_life 半衰期记忆（默认），按相关 × 鲜活 × 重要挑几件；
    # layers 按时间固定带几层（旧）
    recall: Literal["layers", "half_life"] = "half_life"
    half_life_days: float = Field(default=30, ge=1, le=3650)  # 发生过的事过多少天鲜活度减半
    freshness_floor: float = Field(default=0.5, ge=0, le=1)  # 鲜活度保底：再久的事也不低于它
    inject_max: int = Field(default=6, ge=1, le=30)  # 半衰期召回每次最多带几件
    daily_quota: int = Field(default=8, ge=1, le=30)  # 半衰期记忆每人每天最多记几件事
    # --- P2 记忆与联想 ---
    night: bool = True  # 每晚整理合并成一次调用：第一人称日记 + 记忆件 + 挂着的事（+ 心情改写）
    diary_chars: int = Field(default=300, ge=50, le=2000)  # 日记最多多少字
    domains: list[str] = Field(
        default_factory=lambda: ["学术", "日常", "工作", "娱乐", "社交", "身心"], min_length=1
    )
    hanging_max: int = Field(default=5, ge=0, le=10)  # 脑子里挂着的事最多几件
    candidates: int = Field(default=200, ge=10, le=5000)  # 召回时全文索引最多取几件候选
    recent: int = Field(default=30, ge=0, le=500)  # 另外带上最近的几件当候选
    merge_similarity: float = Field(default=0.6, gt=0, le=1)  # 和已有的一件这么像就合并计数
    update_similarity: float = Field(default=0.3, gt=0, le=1)  # 事实这么像（但不到合并）就算更新
    repeat_gap_minutes: int = Field(default=60, ge=1, le=24 * 60)  # 同一次对话：间隔不超过这么久
    digest_chars: int = Field(default=120, ge=0, le=1000)  # 全量经历的习惯摘要多少字（随身卡用）
    work_lookback_days: int = Field(default=14, ge=1, le=90)  # 读自己的工作记录往回看几天
    mood_threshold: float = Field(default=0.3, ge=0, le=1)  # 心情偏离中性这么多才改回忆语气
    retell_max: int = Field(default=3, ge=0, le=10)  # 每晚最多改几件旧记忆的语气和重要度
    links: MemoryLinkRules = MemoryLinkRules()
    diary: DiaryToolRules = DiaryToolRules()


class LifeGuardRules(_Strict):
    """收得住（docs/CHAT.md G9）：互聊收尾、每日条数上限、熔断与每周自检。由代码判断，不花钱。"""

    enabled: bool = True
    daily_messages: int = Field(default=40, ge=0, le=500)  # 成员之间每天最多几句（含分享，UTC 日）
    max_chars: int = Field(default=200, ge=20, le=2000)  # 互聊每句最多保留多少字
    stale_similarity: float = Field(default=0.5, gt=0, le=1)  # 和前面的话太像（二元组相似度）就收尾
    recent_messages: int = Field(default=20, ge=0, le=200)  # 比对最近多少句互聊
    max_failures: int = Field(default=3, ge=1, le=20)  # 连续几次调用出错，当天停掉生活
    budget_alarm: float = Field(default=0.9, gt=0, le=1)  # 生活日上限或聊天月预算用到此比例就当天停
    report_weekday: int = Field(default=1, ge=1, le=7)  # 每周几（ISO，1 = 周一）写上一周的自检
    report_examples: int = Field(default=3, ge=0, le=10)  # 自检报告里最多举几个重复的例子


class StoryStep(_Strict):
    """长线剧情的一步：开始后第几天（整数或 [最少, 最多]）发生什么。

    text 是不带主语的自然话（「嗓子有点疼，泡了杯蜂蜜水」），可用 {name}（她）、{other}（同伴）、
    {domain}（擅长领域）、{new}（新住户）、{gone}（搬走的人）。tags 交给状态插件（P3）按反射表改
    心情精力；本系统不直接改。rest：这天宅在宿舍歇着（生病时），空闲时段不出门。"""

    day: int | list[int] = 0
    text: str = Field(min_length=1, max_length=80)
    tags: list[str] = Field(default_factory=list, max_length=6)
    place: str = Field(default="dorm", max_length=20)  # dorm / 公共房间 id / out（外面）
    rest: bool = False

    @field_validator("day")
    @classmethod
    def _day(cls, v: int | list[int]) -> int | list[int]:
        days = v if isinstance(v, list) else [v]
        if not 1 <= len(days) <= 2 or any(d < 0 or d > 60 for d in days) or days != sorted(days):
            raise ValueError("day 写 0～60 的整数，或 [最少, 最多]")
        return v


class StorySpec(_Strict):
    """生活事件库里的一件事（可以是一步，也可以是有前因后果、持续几天的长线剧情）。

    挑不挑全由程序按时刻派生的随机数决定（可复现、不花钱）：每天的基础概率 chance，档案里提到
    likes 里的词时乘 boost（need_likes 时没提到就不会发生），算 domain 擅长练习，觉醒后再乘。
    trigger：daily 每天挑；arrive / welcome / farewell 由住户变化触发（新成员入住、模型下架）。"""

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    trigger: Literal["daily", "arrive", "welcome", "farewell"] = "daily"
    group: str = Field(default="", max_length=20)  # 频率上限的分组（如 sick：每季度最多一次）
    chance: float = Field(default=0.0, ge=0, le=1)
    hours: list[int] = Field(default_factory=lambda: [9, 22], min_length=2, max_length=2)
    weekdays: list[int] = Field(default_factory=list)  # 0 = 周一；空 = 每天
    months: list[int] = Field(default_factory=list)  # 空 = 全年
    likes: list[str] = Field(default_factory=list, max_length=20)
    boost: float = Field(default=2.0, ge=1, le=10)
    need_likes: bool = False
    domain: str = Field(default="", max_length=20)
    with_other: bool = False  # 找一位同伴一起（两人都记一笔）
    cooldown_days: int = Field(default=7, ge=0, le=400)
    steps: list[StoryStep] = Field(min_length=1, max_length=8)

    @field_validator("hours")
    @classmethod
    def _hours(cls, v: list[int]) -> list[int]:
        if not 0 <= v[0] < v[1] <= 24:
            raise ValueError("hours 写 [开始, 结束)，0～24")
        return v


class LifeWayRules(_Strict):
    """空闲时去哪（L0）：按各自档案里的爱好挑，不再按编号轮换。option 键为「房间:活动」。"""

    enabled: bool = True
    options: dict[str, list[str]] = Field(default_factory=dict)  # 去处 → 档案里提到就更想去的词
    like_weight: float = Field(default=3.0, ge=0, le=20)  # 档案里提到得多的去处，权重最多加这么多
    talent_weight: float = Field(default=2.0, ge=0, le=20)  # 觉醒了的擅长相关的去处再加这么多


class TalentDomain(_Strict):
    """一个擅长领域：档案里的种子词、生活里哪些活动算练习、深耕后楼里的人怎么说她。"""

    words: list[str] = Field(min_length=1, max_length=30)
    acts: list[str] = Field(default_factory=list, max_length=10)  # 去处「房间:活动」或活动 id
    known: str = Field(default="", max_length=40)  # 深耕后的一句自然话，{name} 为她


class TalentTexts(_Strict):
    """擅长各阶段写进生活事件的话（{domain} 领域、{evidence} 依据、{other} 来请教的人）。"""

    awake: str = "突然意识到自己好像挺会{domain}的：{evidence}"
    ask: str = "{other}来找{name}问{domain}上的事，{name}讲了好一会儿"
    stuck: str = "{domain}上卡住了，怎么弄都不对，有点泄气"
    unstuck: str = "{domain}上那个坎忽然想通了"
    rusty: str = "好久没碰{domain}了，手有点生"


class TalentRules(_Strict):
    """觉醒擅长领域（L10）：证据只用人物卡种子 + 生活练习，不看办公室表现、不影响派活。

    萌芽（证据开始聚在一个领域，她自己还没说）→ 觉醒（种子 + 练习够了，说得出依据，经 P6 写进
    档案 likes.擅长，可在成长时间线撤回）→ 深耕（觉醒后一直在练：更常去做、楼里有人来请教）→
    瓶颈与突破（偶尔卡住几天再想通）；长期不练会生疏（经 P6 从档案去掉）。所有成员同一套规则。"""

    enabled: bool = True
    window_days: int = Field(default=30, ge=7, le=120)  # 练习看最近几天
    seed_cap: int = Field(default=3, ge=0, le=10)  # 档案提到的次数最多算几次
    seed_weight: float = Field(default=1.5, ge=0, le=10)
    sprout: float = Field(default=4, ge=1)  # 证据分到这儿算萌芽
    awake: float = Field(default=10, ge=1)  # 证据分到这儿、练习也够才觉醒
    awake_practice: int = Field(default=6, ge=1)  # 觉醒至少练过几回（只有种子不算）
    deep_days: int = Field(default=14, ge=1, le=120)  # 觉醒后再练这么多天进深耕
    deep_practice: int = Field(default=8, ge=1)
    ask_chance: float = Field(default=0.06, ge=0, le=1)  # 深耕后每天有人来请教的概率
    stuck_chance: float = Field(default=0.03, ge=0, le=1)  # 深耕后每天卡住的概率
    stuck_days: list[int] = Field(default_factory=lambda: [2, 5], min_length=2, max_length=2)
    rusty_practice: int = Field(default=1, ge=0)  # 窗口里练习不超过这么多回算生疏
    max_talents: int = Field(default=3, ge=1, le=6)
    story_boost: float = Field(default=1.6, ge=1, le=5)  # 觉醒后相关生活事件的概率乘几
    domains: dict[str, TalentDomain] = Field(default_factory=dict)
    texts: TalentTexts = TalentTexts()


class StoryRules(_Strict):
    """生活事件库 + 长线剧情（P7 生活引擎 L0，docs/LIFE_STORY.md）。

    程序挑事（不花钱，可复现）；只发事件：心情精力 P3、记忆 P2、关系 P4、成长 P6 各自接。
    大部分日子是平淡的：每人每天最多 max_per_day 件，非日常的事有分组冷却（每季度最多病一次）。"""

    enabled: bool = True
    max_per_day: int = Field(default=2, ge=0, le=6)
    group_cooldown_days: dict[str, int] = Field(default_factory=dict)
    # 分组 → 生活阶段（chat.engine.phase）的名字：她正在这些阶段里时不排这一组的事（不会感冒又过敏）
    phase_groups: dict[str, list[str]] = Field(default_factory=dict)
    keep_days: int = Field(default=7, ge=2, le=60)  # 已过去的日程留几天（场景里「最近的事」用）
    scene_days: int = Field(default=2, ge=0, le=7)  # 群聊 / 私聊场景里带最近几天的事
    scene_items: int = Field(default=2, ge=0, le=5)
    scene_chars: int = Field(default=80, ge=0, le=300)
    stories: list[StorySpec] = Field(default_factory=list, max_length=300)
    way: LifeWayRules = LifeWayRules()
    talent: TalentRules = TalentRules()

    @model_validator(mode="after")
    def _unique(self) -> StoryRules:
        ids = [s.id for s in self.stories]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"生活事件 id 重复：{'、'.join(dup)}")
        reserved = sorted(i for i in ids if i.startswith("talent_"))
        if reserved:
            raise ValueError(f"talent_ 开头的 id 留给擅长觉醒用：{'、'.join(reserved)}")
        bad = sorted({s.domain for s in self.stories if s.domain} - set(self.talent.domains))
        if bad:
            raise ValueError(f"生活事件的 domain 不在 talent.domains 里：{'、'.join(bad)}")
        return self


class LifeRules(_Strict):
    """生活引擎（docs/CHAT.md G4）：成员在用户不在时按作息过日子、彼此聊天。"""

    enabled: bool = True
    daily_usd: float = Field(default=0.10, ge=0)  # 生活（含记忆整理）每天最多花多少（UTC 日）
    # 给夜里整理记忆留的钱：白天的聊天、上网、朋友圈花到「上限 − 这笔」就停，记忆整理可以用满上限
    memory_reserve_usd: float = Field(default=0.02, ge=0)
    tick_minutes: int = Field(default=30, ge=5, le=360)  # 电脑开着时多久推进一次
    max_written_per_tick: int = Field(default=2, ge=0, le=10)  # 每次推进最多几件事写成聊天
    max_written_catch_up: int = Field(default=6, ge=0, le=30)  # 开机补算时最多写几件
    catch_up_hours: int = Field(default=24, ge=1, le=24 * 14)  # 补算最多往回看多少小时
    turns: int = Field(default=3, ge=1, le=6)  # 一段成员之间的对话最多几句
    pair_chance: float = Field(default=0.15, ge=0, le=1)  # 每小时有人私下聊天的概率
    estimate_usd: float = Field(default=0.002, ge=0)  # 每句的预估花费（预算检查用）
    max_tokens: int = Field(default=800, ge=64)
    guard: LifeGuardRules = LifeGuardRules()
    story: StoryRules = StoryRules()  # 生活事件库 + 长线剧情 + 擅长觉醒（L0 / L10）


class FeedSpec(_Strict):
    """一个公开订阅源（RSS / Atom）。kind 为 news 的条目分享时不标「未核实」，其余都标。"""

    name: str = Field(min_length=1, max_length=40)
    url: str = Field(pattern=r"^https?://", max_length=500)
    topics: list[str] = Field(default_factory=list, max_length=8)
    kind: Literal["news", "forum", "blog", "video"] = "news"
    enabled: bool = True


class BrowseRules(_Strict):
    """成员上网（docs/CHAT.md G7）：只读公开订阅源；搜索有每日次数上限；花费计入生活每日上限。"""

    enabled: bool = True
    feeds: list[FeedSpec] = Field(default_factory=list, max_length=30)
    refresh_hours: float = Field(default=3, ge=0.5, le=48)  # 多久重新抓一次订阅源
    shares_per_member: int = Field(default=1, ge=0, le=5)  # 每位成员每天最多分享几条
    shares_per_tick: int = Field(default=1, ge=0, le=5)  # 每次推进最多分享几条
    article_chars: int = Field(default=1500, ge=200, le=6000)  # 交给成员读的摘要最多多少字
    max_items_per_feed: int = Field(default=20, ge=1, le=100)
    keep_days: int = Field(default=7, ge=1, le=60)  # 没分享过的条目保留几天
    search_per_day: int = Field(default=5, ge=0, le=20)  # 每天最多联网搜索几次（全体合计）
    search_estimate_usd: float = Field(default=0.01, ge=0)
    estimate_usd: float = Field(default=0.002, ge=0)  # 每条分享的预估花费
    max_tokens: int = Field(default=800, ge=64)
    timeout_s: float = Field(default=10, gt=0, le=60)
    max_feed_kb: int = Field(default=1024, ge=16, le=8192)
    # 分享完有几位接着看、接着聊（2026-10-31 用户：分享就是甩过去让大家看了再讨论）；0 = 不接
    react_members: int = Field(default=2, ge=0, le=4)
    react_turns: int = Field(default=2, ge=0, le=4)  # 接着聊最多几句（每句一次调用，算生活花费）


class MemeSource(_Strict):
    """一个公开热榜（只读、不登录）。kind=json：按 path 找到条目列表、取 key 字段当标题
    （path / key 用「.」分级，数字是第几个、「*」是每一个）；kind=rss：取每条的标题。"""

    name: str = Field(min_length=1, max_length=40)
    url: str = Field(pattern=r"^https?://", max_length=500)
    kind: Literal["json", "rss"] = "json"
    path: str = Field(default="", max_length=80)
    key: str = Field(default="", max_length=40)
    enabled: bool = True


class ClassicMeme(_Strict):
    """经典梗：永远不下架、不算老梗（2026-10-31 用户：NiKo 被称为虾爹、我chovey 之类可以保留）。
    意思空着时，梗雷达第一次联网时搜一下补上（补好的在网页里能看、能改）。"""

    phrase: str = Field(min_length=1, max_length=20)
    aliases: list[str] = Field(default_factory=list, max_length=6)  # 别的说法 / 上句里会出现的词
    meaning: str = Field(default="", max_length=80)
    cue: str = Field(default="", max_length=40)  # 上句：别人说什么时会用到
    reply: str = Field(default="", max_length=40)  # 接法：熟的人怎么接


class MemeRules(_Strict):
    """网络梗（梗雷达，docs/CHAT.md「网络梗」）：每天抓一次公开热榜（$0），每天最多搜几次补充，
    交给档位里最便宜的一位提炼成梗卡（梗、意思、上句 → 接法）；花费计入生活每日上限。
    只用于生活群、私聊、成员互聊（大脑分区的意图卡），工作模式不读。
    防老梗烂梗：只收最近 max_age_days 天内火起来的；fresh_days 天没再上榜就下架（经典梗除外）；
    stale 里的老梗不收、成员说出口前删掉。8 位同一套规则，谁懂哪个梗只看各自的性格分。"""

    enabled: bool = True
    plan: str = "budget"  # 提炼用这个档位里按价格最便宜的一位
    refresh_hours: float = Field(default=24, ge=1, le=168)
    sources: list[MemeSource] = Field(default_factory=list, max_length=20)
    titles_max: int = Field(default=120, ge=10, le=400)  # 交给模型的热榜标题最多几条
    search_per_day: int = Field(
        default=2, ge=0, le=5
    )  # 每天最多联网搜几次（找新梗 1 次 + 查经典梗意思）
    search_query: str = Field(default="最近一周 新出的网络热梗 是什么意思", max_length=60)
    search_estimate_usd: float = Field(default=0.01, ge=0)
    estimate_usd: float = Field(default=0.01, ge=0)  # 提炼一次的预估花费
    max_tokens: int = Field(default=1500, ge=200)
    timeout_s: float = Field(default=10, gt=0, le=60)
    max_kb: int = Field(default=1024, ge=16, le=8192)
    fresh_days: int = Field(default=14, ge=1, le=90)  # 多少天没再上榜就下架
    max_age_days: int = Field(default=90, ge=7, le=730)  # 火起来超过这么久的不收（老梗）
    max_memes: int = Field(default=40, ge=5, le=200)  # 新梗最多存几个（经典梗另算）
    know_base: float = Field(default=0.5, ge=0, le=1)  # 懂一个新梗的基础概率
    know_traits: dict[str, float] = {}  # 性格分（1～5，3 为中）每高 1 分加多少
    know_min: float = Field(default=0.2, ge=0, le=1)
    know_max: float = Field(default=0.95, ge=0, le=1)
    learn_days: float = Field(default=4, ge=0, le=30)  # 新梗刚出来时懂的人少，这么多天后到满
    use_chance: float = Field(default=0.12, ge=0, le=1)  # 意图卡里提一句「最近在玩的梗」的概率
    use_intents: list[str] = Field(default_factory=lambda: ["respond", "joke", "tangent", "blurt"])
    quick_chance: float = Field(
        default=0.5, ge=0, le=1
    )  # 别人说了上句、她懂又有接法：程序直接接（不调模型）
    ask_chance: float = Field(default=0.6, ge=0, le=1)  # 别人玩的梗她不懂：问一句是啥梗
    reuse_days: int = Field(default=3, ge=1, le=30)  # 同一个梗她隔这么多天才再主动用
    classics: list[ClassicMeme] = Field(default_factory=list, max_length=100)
    stale: list[str] = Field(default_factory=list, max_length=200)  # 老梗烂梗：不收、说出口前删掉


class PerceptionMode(_Strict):
    """感官的一种精细程度（docs/PERCEPTION.md）：图片缩到多大、视频取几帧、感知卡写多长。"""

    image_side: int = Field(ge=256, le=4096)  # 图片缩到长边不超过这么多像素
    video_frames: int = Field(ge=1, le=64)  # 视频均匀取几帧（整段交给模型时不用）
    frame_side: int = Field(ge=128, le=2048)  # 每帧缩到长边不超过这么多像素
    grid: bool = False  # 每 4 帧拼成一张 2×2 宫格图（看图 token 约为原来的 1/4）
    native_video: bool = False  # 有带 video_in 标签的模型时，整段视频连声音直接交给它
    max_tokens: int = Field(ge=64, le=8000)  # 感知卡（一条消息的全部内容合计）的输出上限
    estimate_usd: float = Field(ge=0)  # 一条消息的预估花费（预算检查用）


class PerceptionRules(_Strict):
    """成员感官（M11 感知，docs/PERCEPTION.md）：把用户发的图片、语音、视频整理成感知卡给成员读。

    快速（默认）先看；用户要求细看时才补精细，转写稿和缓存都复用。花费计入聊天月预算和总预算，
    另有感官自己的月上限。
    """

    enabled: bool = True
    monthly_usd: float = Field(default=1.5, ge=0)  # 感官每月最多花多少（UTC 自然月）
    fast: PerceptionMode = PerceptionMode(
        image_side=768,
        video_frames=8,
        frame_side=512,
        grid=True,
        max_tokens=600,
        estimate_usd=0.002,
    )
    fine: PerceptionMode = PerceptionMode(
        image_side=1536,
        video_frames=24,
        frame_side=768,
        native_video=True,
        max_tokens=2000,
        estimate_usd=0.03,
    )
    # 用户在消息里写了这些词时直接用精细模式
    fine_words: list[str] = Field(
        default_factory=lambda: ["仔细看", "细看", "看清楚", "看仔细", "仔细听", "听清楚"],
        max_length=30,
    )
    max_audio_s: int = Field(default=600, ge=10, le=3600)  # 语音 / 视频声音最多转写多长
    native_max_mb: float = Field(default=20, gt=0, le=60)  # 整段视频交给模型的大小上限
    card_chars: int = Field(default=800, ge=100, le=4000)  # 每张感知卡最多保留多少字


class ClipperRules(_Strict):
    """办公室的资料浏览器（docs/CLIPPER.md）：打开网页只读抓取（不花钱），搜索走联网搜索。"""

    enabled: bool = True
    timeout_s: float = Field(default=15, gt=0, le=60)
    max_page_kb: int = Field(default=3072, ge=64, le=16384)  # 网页原始大小上限
    max_page_chars: int = Field(default=60000, ge=2000, le=200000)  # 提取后的正文最多显示多少字
    max_clip_chars: int = Field(default=20000, ge=500, le=60000)  # 一份资料最多存多少字（省 token）
    max_clips: int = Field(default=200, ge=1, le=1000)  # 资料夹最多存几份，超出时删最旧的
    search_results: int = Field(default=8, ge=1, le=20)
    search_estimate_usd: float = Field(default=0.01, ge=0)


class MomentsRules(_Strict):
    """朋友圈（docs/CHAT.md G5）。成员发动态、互相点赞评论；花费计入生活每日上限。"""

    enabled: bool = True
    posts_per_member: int = Field(default=1, ge=0, le=5)  # 每位成员每天最多发几条
    posts_per_tick: int = Field(default=1, ge=0, le=5)  # 每次推进最多发几条
    comments_per_tick: int = Field(default=2, ge=0, le=10)  # 每次推进最多写几条评论
    max_comments: int = Field(default=3, ge=0, le=10)  # 成员在一条动态下最多评论几条
    post_misses: int = Field(default=2, ge=1, le=10)  # 成员一天里动态几次没过自查就不再写
    comment_misses: int = Field(default=2, ge=1, le=10)  # 一条动态底下几条评论没过自查就不再评
    like_chance: float = Field(default=0.4, ge=0, le=1)  # 每位成员给一条动态点赞的概率（不花钱）
    react_hours: int = Field(default=24, ge=1, le=168)  # 只对这么多小时内的动态点赞评论
    dedupe_hours: int = Field(default=48, ge=1, le=336)  # 和这么多小时内所有人的动态比对去重
    similarity_max: float = Field(default=0.3, gt=0, le=1)  # 正文相似度达到这个值就不发
    max_chars: int = Field(default=300, ge=50, le=2000)
    max_tokens: int = Field(default=800, ge=64)
    estimate_usd: float = Field(default=0.002, ge=0)


class ProfileRules(_Strict):
    """自我档案（docs/MEMBER_ENGINE.md）：分区 → 谁能改（user / growth 成长 / program 程序）。"""

    sections: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "identity": ["user"],
            "personality": ["user", "growth"],
            "values": ["user", "growth"],
            "style": ["user", "growth"],
            "likes": ["user", "growth"],
            "state": ["user", "program"],
        }
    )


class ContextRules(_Strict):
    """上下文组装器：总长预算、每个来源最多几段、截断后最少保留几个字。"""

    budget_chars: int = Field(default=600, ge=100, le=8000)
    per_source: int = Field(default=2, ge=1, le=10)
    min_piece_chars: int = Field(default=20, ge=1, le=200)


class SchedulerShares(_Strict):
    """各优先级最多能用到每日额度的几成（用户直接找她不设每日额度）。"""

    proactive: float = Field(default=0.85, gt=0, le=1)
    group: float = Field(default=0.7, gt=0, le=1)
    life: float = Field(default=0.5, gt=0, le=1)


class SchedulerRules(_Strict):
    """预算调度器：聊天这一块每天的额度，按优先级分份额。"""

    daily_usd: float = Field(default=0.25, ge=0)
    shares: SchedulerShares = SchedulerShares()


class HeartbeatRules(_Strict):
    enabled: bool = True
    max_actions: int = Field(default=3, ge=0, le=20)  # 每次心跳最多做几件事


class BusRules(_Strict):
    persist: bool = True  # 事件存进 member_events
    # 事件保留几天；None 为一直保留（攒经历数据，以后可能用来训练本地小模型；只存本机）
    keep_days: int | None = Field(default=None, ge=1, le=3650)
    catch_up_days: int = Field(default=14, ge=1, le=365)  # 开机补算最多往回看几天
    # 聊天 / 生活的调用记录里整段提示词保留几天，之后只留最后一段的开头（回复、花费、token 不动）；
    # None 为一直保留。30 天模拟里整段提示词占了库的四分之三
    prompt_keep_days: int | None = Field(default=3, ge=1, le=3650)
    prompt_keep_chars: int = Field(default=300, ge=0, le=5000)


class EvalRules(_Strict):
    """评测集：所有成员相同的参数；同一题两位回复的相似度达到 clash_similarity 算撞型。"""

    max_tokens: int = Field(default=600, ge=64)
    clash_similarity: float = Field(default=0.6, gt=0, le=1)


class CardRules(_Strict):
    """随身卡（M1 自我认知）：每次调模型都带的那一小段，放在上下文组装器的固定前缀里。"""

    budget_chars: int = Field(default=400, ge=50, le=4000)


class VoiceMode(_Strict):
    """说话模式档位（借 dsh maid 的思路）：由性格分决定，所有成员同一张表。"""

    label: str = Field(min_length=1, max_length=8)
    max_segments: int = Field(ge=1, le=6)  # 一次回复最多拆几条
    gap_scale: float = Field(default=1.0, gt=0, le=5)  # 拆条间隔 × 这个倍数（慢性子停得久）
    reply: str = Field(min_length=1, max_length=80)  # 写进说话习惯的「接话」一行


class VoiceCheckRules(_Strict):
    """说出口前自查（程序检查，不调用模型）：重复自己、跟风别人、光附和、口头禅太勤、编造回忆。

    群里没被点名时，跟风 / 光附和的那条直接不发（$0）；其余命中才用成员自己的模型改写一次
    （提示词 ``voice_rewrite``，每月上限 ``monthly_usd``），改不好就发原文。
    """

    enabled: bool = True
    min_chars: int = Field(default=6, ge=1, le=100)  # 去掉表情包记号后短于这么多字的不查
    own_recent: int = Field(default=5, ge=1, le=30)  # 和自己最近几条比
    repeat_similarity: float = Field(default=0.6, gt=0, le=1)
    echo_similarity: float = Field(default=0.5, gt=0, le=1)  # 和别人这一轮说的比
    catchphrase_recent: int = Field(default=3, ge=1, le=20)  # 口头禅最近几条里用过就算太勤
    agree_patterns: list[str] = Field(default_factory=list, max_length=40)  # 开头是附和
    agree_rest_chars: int = Field(default=8, ge=0, le=60)  # 附和之外剩下的字少于这么多算「光附和」
    follow_similarity: float = Field(default=0.25, gt=0, le=1)  # 附和开头 + 剩下的像别人说的：接龙
    memory_patterns: list[str] = Field(default_factory=list, max_length=40)  # 提到共同回忆
    opening_chars: int = Field(default=3, ge=2, le=10)  # 开头这么多字和最近几条一样算套句式
    opening_times: int = Field(default=2, ge=1, le=10)
    post_repeat_similarity: float = Field(default=0.4, gt=0, le=1)  # 朋友圈和自己最近的动态比
    rechew_hours: int = Field(default=72, ge=1, le=24 * 30)  # 反复提同一件事：看最近几小时
    rechew_messages: int = Field(default=60, ge=5, le=500)
    rechew_chars: int = Field(default=4, ge=2, le=8)  # 几个字的片段算一件事（如「番茄牛腩」）
    rechew_times: int = Field(default=2, ge=1, le=20)  # 最近已经说过这么多次就别再提
    moments_need_event: bool = True  # 没发生什么事（只有一个人待着、没活干）的日子不发朋友圈
    monthly_usd: float = Field(default=0.05, ge=0)
    estimate_usd: float = Field(default=0.0005, ge=0)
    max_tokens: int = Field(default=400, ge=64)


class VoiceRules(_Strict):
    """P5 决定与表达·表达（docs/MEMBER_ENGINE.md「说话风格」）：从自我档案的说话风格抽「说话习惯」，
    按性格分定说话模式档位，接管聊天的 ActionPolicy。只用于生活群和私聊；
    8 位同一套规则、同一个字数上限。
    """

    enabled: bool = True
    budget_chars: int = Field(default=260, ge=60, le=1000)  # 说话习惯段最多多少字（8 位相同）
    samples: int = Field(default=2, ge=0, le=4)  # 每次带几句示范（轮换，防止照抄）
    sample_chars: int = Field(default=50, ge=10, le=200)  # 太长的示范句不带
    # 句子长短 → 一条大约多少字以内
    lengths: dict[str, int] = Field(default_factory=lambda: {"短": 25, "中": 45, "长": 80})
    # 外向分 → 档位：≤ quiet_max 话少，≥ chatty_min 话多，其余平常
    quiet_max: int = Field(default=2, ge=1, le=5)
    chatty_min: int = Field(default=4, ge=1, le=5)
    modes: dict[str, VoiceMode] = Field(default_factory=dict)
    # 幽默分 → 表情包频率（≤2 / 3 / ≥4）
    sticker_habits: list[str] = Field(default_factory=list, max_length=3)
    check: VoiceCheckRules = VoiceCheckRules()

    @field_validator("modes")
    @classmethod
    def _modes(cls, v: dict[str, VoiceMode]) -> dict[str, VoiceMode]:
        missing = {"quiet", "normal", "chatty"} - set(v)
        if v and missing:
            raise ValueError(f"缺少档位：{', '.join(sorted(missing))}")
        return v


class StateLayerRules(_Strict):
    """心情或精力的快慢两层：每次冲击 slow_share 落到慢层，其余落到快层；两层各自按半衰期回到 0。"""

    fast_half_life_hours: float = Field(default=2.0, gt=0, le=72)
    slow_half_life_hours: float = Field(default=48.0, gt=0, le=24 * 30)
    slow_share: float = Field(default=0.2, ge=0, le=1)
    fast_cap: float = Field(default=0.8, gt=0, le=2)  # 快层最多偏离多少
    slow_cap: float = Field(default=0.4, gt=0, le=2)  # 慢层最多偏离多少


class CircadianRules(_Strict):
    """精力的作息基线（0～1）：醒来后爬升到高点，午后小低谷，睡前回落，睡着时最低。"""

    asleep: float = Field(default=0.15, ge=0, le=1)
    waking: float = Field(default=0.45, ge=0, le=1)  # 刚醒
    peak: float = Field(default=0.85, ge=0, le=1)
    bedtime: float = Field(default=0.35, ge=0, le=1)  # 快睡时
    ramp_hours: float = Field(default=1.5, gt=0, le=6)  # 醒来后几小时到高点（夜猫子加倍）
    wind_down_hours: float = Field(default=2.0, gt=0, le=6)  # 睡前几小时开始回落（夜猫子减半）
    dip: float = Field(default=0.1, ge=0, le=0.5)  # 午后低谷
    dip_hours: tuple[int, int] = (13, 15)


class BaselineRules(_Strict):
    """从自我档案读基线（人物卡第 5 章「情绪」、第 8 章「日常」、五项性格分）；只看这些词。"""

    bright_words: list[str] = ["乐观", "开朗", "想得开", "没心没肺", "很快就忘", "很快就好"]
    dark_words: list[str] = ["内耗", "焦虑", "敏感", "多想", "钻牛角尖", "想太多"]
    calm_words: list[str] = ["很少生气", "不太生气", "平静", "淡定", "不会大吼", "不容易生气"]
    wake_words: list[str] = ["起床", "醒", "闹钟", "起来", "睡到"]
    sleep_words: list[str] = ["睡觉", "入睡", "上床", "睡前", "熄灯", "前睡", "不晚于", "洗漱"]
    night_words: list[str] = ["夜猫", "熬夜", "晚上反而", "容易精神", "越晚越精神", "深夜才睡"]


class ReflexRule(_Strict):
    """本能反射规则表的一行：刺激带齐 tags 里的标签且满足条件时，改心情 / 精力，给出动作和说明。

    标签：spoke 自己说话 / from_user 你发的 / from_member 别的成员发的 / dm 私聊 / mentioned 被 @ /
    everyone 叫所有人 / criticized 被直接批评 / group_criticized 大家一起被批评 / praised 被夸 /
    life、life.gather、life.pair、life.solo 生活里的事 / work 在办公室干完活 / perceive 看过听过的。
    """

    name: str
    tags: list[str] = Field(min_length=1)
    night: bool | None = None  # true：她本该在睡觉时才触发；false：醒着时才触发
    energy_below: float | None = Field(default=None, ge=0, le=1)
    mood_below: float | None = Field(default=None, ge=-1, le=1)
    mood: float = Field(default=0.0, ge=-1, le=1)  # 乘以这位成员的情绪敏感度
    energy: float = Field(default=0.0, ge=-1, le=1)
    act: str = ""  # 给之后的「决定与表达」：reply_now 秒回 / sleepy 犯困 / flustered 害羞 …
    note: str = ""  # 记成「刚才…」放进状态那一行


class StateRules(_Strict):
    """状态（P3，docs/MEMBER_ENGINE.md）：心情和精力全由程序按事件算，快慢两层、会回到基线。"""

    enabled: bool = True
    mood: StateLayerRules = StateLayerRules()
    energy: StateLayerRules = StateLayerRules(
        fast_half_life_hours=1.0, slow_half_life_hours=10.0, slow_share=0.5, slow_cap=0.5
    )
    circadian: CircadianRules = CircadianRules()
    baseline: BaselineRules = BaselineRules()
    criticism_words: list[str] = []
    praise_words: list[str] = []
    reflexes: list[ReflexRule] = []
    recent_keep: int = Field(default=5, ge=0, le=50)  # 记最近几次反射
    recent_hours: float = Field(
        default=2.0, ge=0, le=48
    )  # 多久以内的反射写进状态那一行（默认 2 小时）
    # 状态那一行：{mood} {energy} 换成下面的标签，{recent} 换成 recent_format；总长 ≤ context_chars
    mood_labels: list[tuple[float, str]] = []  # (下限, 标签)，从高到低，取第一个 ≥ 下限的
    energy_labels: list[tuple[float, str]] = []
    line_format: str = "{mood}，{energy}{recent}"
    recent_format: str = "；{note}"
    context_chars: int = Field(default=80, ge=0, le=300)


class AboutRules(_Strict):
    """关于你（P8，docs/MEMBER_ENGINE.md「P8 关于你」）：8 位成员共用的一份用户画像，只放事实、
    来源、证据和把握度；推测会变淡，你亲口说的不变淡。只用于私聊和生活群，工作模式不挂载。"""

    enabled: bool = True
    # 你的初始身份（10-31 用户定：老板兼房东）：私聊和群聊每次都接在「关于你」最前面
    identity: str = Field(default="", max_length=120)
    # 画像分哪几个领域（可以按领域关掉）；夜间整理给的领域不在表里时归最后一个
    domains: list[str] = Field(
        default=[
            "称呼与身份",
            "学业",
            "工作",
            "兴趣爱好",
            "口味",
            "作息",
            "聊天偏好",
            "计划",
            "其他",
        ],
        min_length=1,
    )
    # 不推测的领域：线索里出现这些词就不记（你在画像页自己写的不拦）
    blocked: dict[str, list[str]] = {}
    clues_per_night: int = Field(default=3, ge=0, le=10)  # 每位成员每晚最多交几条线索
    fact_chars: int = Field(default=40, ge=10, le=200)  # 一条事实最多几个字
    said_confidence: float = Field(default=0.9, gt=0, le=1)  # 你亲口说的：把握度，不变淡
    inferred_confidence: float = Field(default=0.5, gt=0, le=1)  # 推测：起步把握度
    evidence_bonus: float = Field(default=0.15, ge=0, le=1)  # 推测每多一条不同的证据加多少
    inferred_cap: float = Field(default=0.85, gt=0, le=1)  # 推测最多到多少
    half_life_days: float = Field(default=30.0, gt=0)  # 推测多久没再出现证据就淡一半
    faded_below: float = Field(default=0.25, ge=0, le=1)  # 低于这个算「变淡了」，聊天不带
    merge_similarity: float = Field(default=0.55, gt=0, le=1)  # 同领域、这么像算同一条
    reject_similarity: float = Field(default=0.5, gt=0, le=1)  # 和你点过「不对」的这么像就不再记
    max_facts: int = Field(default=200, ge=1, le=2000)
    max_evidence: int = Field(default=5, ge=1, le=50)  # 每条最多留几条证据
    # 聊天时带几条、多少字（8 位相同）；只带和当前话题相关的
    inject_max: int = Field(default=3, ge=0, le=10)
    context_chars: int = Field(default=100, ge=0, le=400)
    min_relevance: float = Field(default=0.1, ge=0, le=1)
    header: str = "【关于用户（你知道的事，用得上再提）】"
    # 知情范围（2026-10-31 拟人插件调研第 1 项）：你在私聊里只告诉 A 的事只有 A 知道；在群里说的
    # 群里的人都知道；你在画像页写的大家都知道。别人只能经同事私下聊天「听 A 说」辗转知道，
    # 每次两人聊天最多传 gossip_max 条、每条按 gossip_chance；说了「别告诉别人」之类的永远不传。
    scope: bool = True
    gossip_chance: float = Field(default=0.15, ge=0, le=1)
    gossip_max: int = Field(default=1, ge=0, le=5)
    secret_words: list[str] = Field(
        default=[
            "别告诉别人",
            "别跟别人说",
            "别和别人说",
            "不要告诉别人",
            "不要跟别人说",
            "只告诉你",
            "只跟你说",
            "帮我保密",
            "替我保密",
            "要保密",
            "别说出去",
            "不要说出去",
            "我们的秘密",
            "咱俩的秘密",
        ],
        max_length=60,
    )
    maybe_below: float = Field(default=0.6, ge=0, le=1)  # 把握度低于这个的在聊天里标「可能」
    # 第二块：用户状态（正在放的歌、电脑空闲、在玩游戏、课表 DDL、邮件栏），每项默认关
    status_header: str = "【用户此刻（用户自己打开的，自然带出，别直接念）】"
    status_chars: int = Field(default=80, ge=0, le=300)  # 8 位相同
    idle_minutes: float = Field(default=10.0, gt=0)  # 电脑多久没人动算「走开了」
    ddl_days: int = Field(default=7, ge=0, le=60)  # 多少天内的 DDL 才提
    games: dict[str, str] = {}  # 程序文件名 → 游戏名（Steam / Epic 装的不用写）


class GrowthReviewRules(_Strict):
    """成长的评审（借 dsh answer-reviewer）：另一位成员给每条改动打分，低于 min_score 不改；
    不自评（评审永远不是本人）；每周才评一次；提议格式不对或全被拦下时最多重试 max_retries 次。"""

    enabled: bool = True
    min_score: int = Field(default=3, ge=1, le=5)  # 1～5 分，低于它这条不改
    max_retries: int = Field(default=1, ge=0, le=1)


class GrowthHabitRules(_Strict):
    """习惯计数（M17）：从全部经历累加；次数和跨的周数都够才算习惯（所有成员相同）。"""

    min_count: int = Field(default=5, ge=1, le=1000)
    min_weeks: int = Field(default=3, ge=1, le=52)
    top: int = Field(default=8, ge=1, le=30)  # 最多列几项


class GrowthReportRules(_Strict):
    """月度「这个月的我」报告：程序按记录数出来，不调用模型。"""

    enabled: bool = True
    keep: int = Field(default=24, ge=1, le=120)  # 每人最多留几份


class GrowthBookRules(_Strict):
    """成长总账（docs/MEMBER_ENGINE.md「成长总账」）：把各模块记下的变化排成一条时间线（只读汇总，
    各模块仍各写各的），月报算全量，最近的变化给她自己一行。不花钱。"""

    enabled: bool = True
    keep: int = Field(default=200, ge=10, le=2000)  # 网页上最多列多少条
    recent_days: int = Field(default=14, ge=1, le=90)  # 「这阵子」算最近几天
    recent_max: int = Field(default=2, ge=0, le=5)  # 给她自己的那一行最多带几件（0 = 不带）
    context_chars: int = Field(default=50, ge=0, le=200)  # 那一行最多几个字（算在 600 字里）


class GrowthPlasticRules(_Strict):
    """发育期可塑性（成长 G2）：新来的变得快，待久了变得慢。

    8 位同一套规则，差别只来自入职时间和性格。"""

    enabled: bool = True
    young_days: int = Field(default=60, ge=0, le=3650)  # 入职头这么多天是发育期（照原规则）
    settled_changes: int = Field(default=2, ge=1, le=5)  # 定型以后每周最多改几条
    settle_days: int = Field(default=60, ge=1, le=730)  # 定型以后性格分两次改动至少隔几天
    min_gap_days: int = Field(default=30, ge=1, le=730)  # 再快也至少隔这么多天
    # 每比 3 分高一分，间隔乘上 (1 + 系数)：好奇、外向变得快，较真变得慢
    trait_speed: dict[str, float] = {"好奇": -0.1, "外向": -0.05, "较真": 0.1}


class GrowthVitalsRules(_Strict):
    """成长的防退化阈值（成长 G3，docs/MEMBER_ENGINE.md「半年快进与防退化」）：半年快进和加速模拟
    期末超过这些就算出问题（测试失败、报告里标红）。全程序算，不花钱。"""

    overlap_max: float = Field(default=0.4, ge=0, le=1)  # 两位成员的长处 / 喜好最多重合几成
    extreme_gain: float = Field(default=0.15, ge=0, le=1)  # 五项分在 1 或 5 的占比最多多几成
    push_max: float = Field(default=1.0, ge=0, le=4)  # 一项性格全员平均最多移动几格
    moved_max: int = Field(default=4, ge=0, le=20)  # 一位成员五项分一共最多移动几格
    share_slack: float = Field(default=0.05, ge=0, le=1)  # 受你影响的占比比上限多出这么多才算
    catchphrase_left: float = Field(default=0.3, ge=0, le=1)  # 能用的口头禅至少还有期初的几成
    kaomoji_left: int = Field(default=2, ge=0, le=20)  # 颜文字至少还有几个
    dupes_max: float = Field(default=0.1, ge=0, le=1)  # 本该合并却各记一条的记忆件最多占几成


class GrowthRules(_Strict):
    """P6 成长（docs/MEMBER_ENGINE.md「P6 成长」）：每周用成员自己的模型读一遍上周的经历，
    最多提几条对自我档案的改动，每条必须引用真实经历；程序把关后才写进档案（author = growth）。

    只用于生活模式；8 位同一套规则、同一个预算。
    """

    enabled: bool = True
    weekday: int = Field(default=1, ge=1, le=7)  # 每周几（ISO，1 = 周一）反思上一周
    min_days: int = Field(default=3, ge=1, le=7)  # 上周至少几天有经历才反思，否则跳过并记原因
    book: GrowthBookRules = GrowthBookRules()
    vitals: GrowthVitalsRules = GrowthVitalsRules()
    plastic: GrowthPlasticRules = GrowthPlasticRules()
    reconcile: bool = True  # 对账（成长 G2）：口癖、学来的说法、习惯和档案对不上时写进每周经历
    min_lines: int = Field(default=5, ge=1, le=100)  # 上周至少几条经历（日记、记忆件、你说的话）
    max_changes: int = Field(default=3, ge=1, le=5)  # 每周最多改几条
    material_chars: int = Field(default=4000, ge=500, le=20000)  # 周总结最多多少字
    line_chars: int = Field(default=120, ge=20, le=500)  # 每条经历最多多少字
    user_lines: int = Field(default=12, ge=0, le=50)  # 最多带你说过的几句话
    # 引用的原话至少有这么多（两字片段）出现在所引的经历里（照抄为 1）
    evidence_overlap: float = Field(default=0.6, gt=0, le=1)
    # 能改的列表项（分区 → 键）；五项性格分（personality.五项）另按「一格」改
    lists: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "personality": ["长处", "小毛病"],
            "values": ["最看重"],
            "style": ["口头禅"],
            "likes": ["最爱", "讨厌"],
        }
    )
    list_max: int = Field(default=8, ge=1, le=30)  # 每个列表最多几项
    list_keep: int = Field(default=2, ge=0, le=30)  # 去掉一项后至少还留几项（不会被删光）
    item_chars: int = Field(default=20, ge=2, le=60)  # 新加的一项最多多少字
    unique_lists: list[str] = Field(default_factory=lambda: ["style.口头禅"])  # 不和别人重样的列表
    core_monthly_steps: int = Field(default=1, ge=0, le=5)  # 核心性格（五项）每月最多改几格
    anchor_steps: int = Field(default=2, ge=0, le=4)  # 成长让一项性格一共最多离人物卡几格
    same_way: float = Field(
        default=0.4, gt=0, le=1
    )  # 这么多成员（按 8 人比例）往同一边变过这一项，别人先不跟
    min_distance: int = Field(default=2, ge=0, le=20)  # 8 人五项分两两至少差几（各项差的和）
    user_share: float = Field(default=0.3, ge=0, le=1)  # 受你影响的改动最多占几成
    share_window_weeks: int = Field(default=8, ge=1, le=52)  # 按最近几周的改动算占比
    share_floor: int = Field(default=10, ge=1, le=100)  # 改动不到这么多条时按这么多条算（起步时）
    review: GrowthReviewRules = GrowthReviewRules()
    habits: GrowthHabitRules = GrowthHabitRules()
    report: GrowthReportRules = GrowthReportRules()
    backups: int = Field(default=8, ge=1, le=52)  # 每周本地备份留几份
    timeline_keep: int = Field(default=500, ge=10, le=5000)  # 成长时间线每人最多留几条
    max_tokens: int = Field(default=900, ge=64)
    estimate_usd: float = Field(default=0.002, ge=0)  # 每次调用的预估花费（8 位相同）


class RelationStage(_Strict):
    """和你的一个阶段：一起聊过的天数、来回次数、从认识到现在跨的周数都够了才到这一阶段。"""

    name: str = Field(min_length=1, max_length=10)
    min_days: int = Field(default=0, ge=0, le=3650)
    min_exchanges: int = Field(default=0, ge=0, le=100000)
    min_weeks: int = Field(default=0, ge=0, le=520)


class RelationWeights(_Strict):
    """每种共同经历记几分（所有成员相同）。"""

    dm: float = Field(default=1.0, ge=0, le=10)  # 你私聊她，她回了
    group: float = Field(default=0.5, ge=0, le=10)  # 群里你说话，她接了
    gather: float = Field(default=1.0, ge=0, le=10)  # 生活里碰到一起（吃饭、锻炼……）
    pair: float = Field(default=2.0, ge=0, le=10)  # 生活里两人私下聊天
    chat: float = Field(default=0.3, ge=0, le=10)  # 生活群 / 私聊里一来一回
    work: float = Field(default=0.5, ge=0, le=10)  # 一起在办公室干过同一件活（只读工作记录）


class InsightDepth(_Strict):
    """和你到了某一阶段时，看法能写多深（第 i 条对应第 i 个阶段，阶段比条目多时用最后一条）。"""

    views: int = Field(default=3, ge=1, le=8)  # 最多几句
    chars: int = Field(default=40, ge=10, le=120)  # 每句最多几个字
    context: int = Field(default=100, ge=0, le=400)  # 私聊、生活群里带的看法最多几个字
    hint: str = Field(default="", max_length=200)  # 写进提示词：这个阶段说到多深


class InsightRules(_Strict):
    """P4 关系·对你的见解：每周一次，成员用自己的模型按自己的性格和三观，读「关于你」画像和
    最近和你相处的片段（私聊里的原话，含你对她看法的反应、你在网页上回她的话），在上周的看法上
    保留、加深、改观或放下，开头都是「我觉得」。越熟写得越多越深（``depth``）。程序把关：
    必须有依据（画像条目或相处片段）、不推测敏感领域、不只是复述。8 位同一套规则、同一个预算。"""

    enabled: bool = True
    depth: list[InsightDepth] = Field(
        default_factory=lambda: [
            InsightDepth(views=1, chars=30, context=60),
            InsightDepth(views=3, chars=40, context=100),
            InsightDepth(views=5, chars=60, context=200),
        ],
        min_length=1,
    )
    facts_max: int = Field(default=20, ge=0, le=60)  # 最多带画像里几条（按把握度）
    moments_max: int = Field(default=12, ge=0, le=40)  # 最多带几段私聊里的相处片段
    moment_chars: int = Field(default=60, ge=10, le=200)  # 每段片段最多几个字
    moments_days: int = Field(default=14, ge=1, le=90)  # 片段从最近几天的私聊里挑
    min_facts: int = Field(default=2, ge=0, le=60)  # 画像条目 + 相处片段少于这么多就不想
    rehash_similarity: float = Field(default=0.75, gt=0, le=1)  # 和所引依据这么像算只是复述
    history_max: int = Field(default=6, ge=0, le=50)  # 每句看法留几版以前的说法（时间线）
    reply_chars: int = Field(default=80, ge=1, le=300)  # 你在看法下随手回的一句最多几个字
    header: str = (
        "我对你的看法（只是我自己的看法，可能不对；合适的时候可以顺口说出来，用「我觉得」，"
        "不用每次都提）："
    )
    keep_log: int = Field(default=20, ge=1, le=200)  # 每人留最近几周的见解记录
    max_tokens: int = Field(default=900, ge=64)
    estimate_usd: float = Field(default=0.002, ge=0)  # 每次调用的预估花费（8 位相同）

    def at(self, level: int) -> InsightDepth:
        return self.depth[min(max(level, 0), len(self.depth) - 1)]


class RelationRules(_Strict):
    """P4 关系（docs/MEMBER_ENGINE.md「P4 关系」）：和你的阶段、同事之间的亲近度，
    全由程序按真实的共同经历累积，不调用模型。只用于生活模式；只影响生活里谁和谁聊，
    不影响工作派活。8 位规则相同。"""

    enabled: bool = True
    half_life_days: float = Field(default=60.0, gt=0, le=3650)  # 亲近度多久不来往淡一半
    # 亲近度 = 1 − e^(−分数 / scale)：和你按 user_scale，同事之间按 scale
    scale: float = Field(default=12.0, gt=0, le=1000)
    user_scale: float = Field(default=40.0, gt=0, le=1000)
    reply_window_minutes: int = Field(default=30, ge=1, le=1440)  # 多久以内接话算一来一回
    user_daily_cap: float = Field(default=6.0, ge=0, le=100)  # 和你每天最多记几分（刷不出老朋友）
    peer_daily_cap: float = Field(default=4.0, ge=0, le=100)  # 两位同事之间每天最多记几分
    weights: RelationWeights = RelationWeights()
    stages: list[RelationStage] = Field(
        default_factory=lambda: [
            RelationStage(name="新邻居"),
            RelationStage(name="熟悉", min_days=3, min_exchanges=15),
            RelationStage(name="老朋友", min_days=20, min_exchanges=150, min_weeks=6),
        ],
        min_length=1,
    )
    quiet_days: int = Field(default=14, ge=1, le=365)  # 多少天没来往算「有阵子没聊了」
    # 同事之间的亲近度标签：(下限, 标签)，从高到低，取第一个 ≥ 下限的
    peer_labels: list[tuple[float, str]] = Field(
        default_factory=lambda: [(0.6, "走得近"), (0.3, "挺熟"), (0.1, "点头之交"), (0.0, "不太熟")]
    )
    pair_bias: float = Field(default=2.0, ge=0, le=20)  # 生活里挑谁和谁聊：权重 = 1 + 它 × 亲近度
    line_format: str = "和你（程序按一起经历的事算的，别直接念）：{stage}，认识第 {known} 天{quiet}"
    quiet_format: str = "，有阵子没聊了"
    context_chars: int = Field(default=60, ge=0, le=200)  # 和你那一行最多几个字（8 位相同）
    scene_chars: int = Field(default=80, ge=0, le=300)  # 生活场景里「交情」那一行最多几个字
    insight: InsightRules = InsightRules()


class DriveRule(_Strict):
    """一种驱力（下丘脑）：每小时自己涨多少（再乘性格系数），被什么事满足多少。"""

    label: str  # 写进意图卡的说法，如「想找人说说话」
    rate: float = Field(ge=0, le=1)  # 每小时涨多少（0～1 的刻度）
    trait: str | None = None  # 哪项性格分放大涨速（五项之一；3 分 1 倍，1 分 0.6，5 分 1.4）
    satisfy: dict[str, float] = {}  # 事件 → 降多少：spoke / gathered / pursued / explored


class GoalRules(_Strict):
    """长线目标（前额叶）：从喜好里挑一两件「在做的事」，空闲时去做，会进步、会卡住、会有里程碑。"""

    max_active: int = Field(default=2, ge=0, le=5)
    fallback: list[str] = ["学一门新手艺", "读完一本厚书", "把房间布置成自己喜欢的样子"]
    gain: tuple[float, float] = (0.3, 1.2)  # 每做一次涨多少经验（随机）
    setback_chance: float = Field(default=0.12, ge=0, le=1)  # 做着做着卡住
    levels: list[float] = [4, 12, 25, 45, 80]  # 经验到这些数就过一个坎（一个月大约过两三个）
    # 主动做决定：卡住次数到了、又还没什么进展，有一定概率放下这件事、换一件（较真的人更坚持）
    give_up_after: int = Field(default=3, ge=1, le=50)
    give_up_chance: float = Field(default=0.4, ge=0, le=1)
    # 目标是从人物卡里摘的原句，说出口时要换成自己的话（10-31 真模型试跑：有人照抄「有人站得住」）
    say_line: str = "（这是你的打算：说出口就讲眼下具体在弄的一小步，用你自己的话，别照搬这句）"
    stages: list[str] = [
        "刚开始",
        "有了点头绪",
        "往前走了一步",
        "有点样子了",
        "快成了",
        "成了",
    ]


class IntentRule(_Strict):
    """一种说话意图：基础权重、受哪些量放大或压低、写进意图卡的说法。"""

    label: str  # 写进意图卡，如「讲讲你自己的事」
    base: float = Field(ge=0)
    traits: dict[str, float] = {}  # 性格分（减 3 后）× 系数
    drives: dict[str, float] = {}  # 驱力（0～1）× 系数
    mood: float = 0.0  # 心情（-1～1）× 系数
    energy: float = 0.0  # 精力（0～1，减 0.5 后）× 系数
    # 只有在有这类念头时才可选：own（自己的事）/ claim（对方说了个看法）/ sad / whim；
    # last：有人刚说了话才可选（顺着那句话歪楼）
    needs: str | None = None


class RewardRules(_Strict):
    """奖赏（多巴胺）：盼着一件事 → 到时候兑现（满足）/ 落空（失落）/ 没想到的好事（惊喜）。
    心情的变化交给 P3 的反射规则表（标签 anticipate / reward / surprise / letdown）。"""

    near: float = Field(default=0.75, ge=0, le=1)  # 在做的事离下一个坎还差多少时开始盼
    plan_chance: float = Field(default=0.2, ge=0, le=1)  # 周四到周六盼周末的小计划的概率
    met_chance: float = Field(default=0.7, ge=0, le=1)  # 周末小计划到时候成行的概率
    ahead_days: float = Field(default=2.0, gt=0, le=14)  # 盼的事几天后见分晓
    plans: list[str] = ["去天台看星星", "睡个懒觉", "去食堂吃顿好的", "和谁约着打一局游戏"]


class EmpathyRules(_Strict):
    """共情：听到别人难过 / 高兴，自己的心情跟着动一下（概率看温和分和亲近度）。"""

    sad_words: list[str] = ["难过", "好累", "崩溃", "烦死", "失眠", "哭", "不开心", "委屈", "焦虑"]
    joy_words: list[str] = ["太好了", "成功了", "开心", "终于", "好耶", "过了"]
    base: float = Field(default=0.35, ge=0, le=1)
    gentle: float = Field(default=0.12, ge=0, le=1)  # 温和分每高 1 分加多少
    close: float = Field(default=0.4, ge=0, le=1)  # × 亲近度


class MetaRules(_Strict):
    """元认知：察觉自己最近老是一个套路（老抱怨、老敷衍……），主动压一压、换个方式。"""

    window: int = Field(default=8, ge=3, le=30)
    threshold: int = Field(default=4, ge=2, le=30)
    damp: float = Field(default=0.3, ge=0, le=1)
    notes: dict[str, str] = {
        "vent": "最近老在抱怨",
        "brief": "最近回人回得很敷衍",
        "disagree": "最近老在抬杠",
        "share": "最近老说自己的事，没怎么问别人",
        "joke": "最近老在开玩笑，没好好接话",
    }
    swap: dict[str, str] = {"share": "ask", "vent": "share", "brief": "respond"}  # 压下去后多做的


class SwayRules(_Strict):
    """性格天平①：同一个人每天、每个场合表现不一样（Fleeson 整体特质论：性格是一段分布，
    不是一个点；Mischel & Shoda 认知情感人格系统：「如果……就……」的情境特征）。"""

    daily: float = Field(default=0.5, ge=0, le=2)  # 每项性格分每天上下浮动（标准差，单位：分）
    close: dict[str, float] = {"外向": 1.0, "幽默": 0.6}  # 对亲近的人（× 亲近度）
    tired: dict[str, float] = {"较真": -0.8, "幽默": -0.6, "温和": -0.5}  # 累了（× 疲惫程度）
    mood: dict[str, float] = {"外向": 0.8, "幽默": 0.6, "温和": 0.3}  # × 心情（-1～1）


class PoleRule(_Strict):
    """对立情绪的一端：强度 = Σ 系数 × 输入。输入有 social / novelty / express / mastery 驱力，
    energy、tired（= 1 - 精力），mood、sad（心情低落的程度），trait:外向 等（= (分数 - 3) / 2），
    base（常数 1）。"""

    label: str
    weights: dict[str, float]


class TensionRule(_Strict):
    a: PoleRule
    b: PoleRule


class AmbivalenceRules(_Strict):
    """性格天平②：两种相反的想法同时都挺强（Larsen & McGraw 2001：人能同时又高兴又难过；
    Schwartz 价值环：相邻的价值相容、对面的价值相冲）。两端都过线时意图卡写一句「心里有点矛盾」。"""

    both_above: float = Field(default=0.45, ge=0, le=2)
    pairs: list[TensionRule] = []


class Strategy(_Strict):
    line: str  # 写进意图卡
    traits: dict[str, float]  # 谁更习惯这样处理（性格分减 3 后 × 系数，取最高）
    half_life: float = Field(default=1.0, gt=0, le=10)  # × 余波半衰期（反刍的更久，往好处想的更快）


class EpisodeRules(_Strict):
    """情绪化处理：一件事带来的情绪会拖一阵（余波），按各人习惯的方式消化（Gross 情绪调节过程模型：
    重新评价 / 压着不说；Nolen-Hoeksema 反刍；找人倾诉）。余波还在时会冒出来、会影响说话。"""

    feels: dict[str, str] = {  # 信号 → 余波的感受
        "letdown": "有点失落",
        "surprise": "还挺高兴",
        "reward": "挺满足",
        "empathy_sad": "替人难受",
    }
    half_life_hours: float = Field(default=8.0, gt=0, le=240)
    keep: int = Field(default=3, ge=1, le=10)
    strategies: dict[str, Strategy] = {}


class ConfabRules(_Strict):
    """先果后因：先有冲动、先做了，理由是事后补的，而且补的理由常常不是真原因。
    依据 Gazzaniga 解释器；Nisbett & Wilson 1977；Haidt 2001 情绪的狗和理性的尾巴；
    Schwarz & Clore 情绪当信息（心情归错因）。"""

    chance: float = Field(default=0.25, ge=0, le=1)  # 意图卡写一句「事后会给自己的理由」的概率
    reasons: dict[str, list[str]] = {}  # 意图 → 事后理由（随便挑一条，不一定是真原因）
    misattribute: float = Field(
        default=0.6, ge=0, le=1
    )  # 累了 / 心情差时把烦躁归到眼前这事上的概率


class WhimRules(_Strict):
    """突发念头：没来由地冒出一个想法（Christoff 2016 自发思维；Berntsen 不由自主的回忆），
    冲动的人更可能说出来、甚至说干就干（变成一个小盼头）。"""

    chance: float = Field(default=0.06, ge=0, le=1)  # 每次说话前冒出念头的基础概率
    traits: dict[str, float] = {"外向": 0.015, "好奇": 0.02, "较真": -0.01}  # × (分数 - 3)
    act_chance: float = Field(default=0.3, ge=0, le=1)  # 冒出来后说干就干（记成盼头）的概率
    pool: list[str] = []
    # 去模板化：每位只从公共念头里分到一部分（按成员 id 派生，不认人），再加上由喜好来的「突然想……」
    share: float = Field(default=0.5, gt=0, le=1)


class AssocRules(_Strict):
    """发散联想：顺着一句话想到别的，越好奇跳得越远（Mednick 1962 联想层级：创造力高的人联想
    分布更平；扩散激活）。还会被一句话勾起一段旧记忆（不由自主的回忆，线索触发）。"""

    chance: float = Field(default=0.2, ge=0, le=1)
    curious: float = Field(default=0.06, ge=0, le=1)  # 好奇分每高 1 分加多少
    second_hop: float = Field(default=0.25, ge=0, le=1)  # 再跳一步（远联想）的概率，× 好奇系数
    cue_memory: float = Field(default=0.3, ge=0, le=1)  # 话里有和旧记忆相同的词时被勾起的概率
    links: dict[str, list[str]] = {}  # 通用联想网（不认人）；她自己的喜好和记忆另算
    stop: list[str] = []  # 勾记忆时不算的常用词
    # 记忆走样：勾起的旧事超过这么多天，有一定概率记岔细节（Loftus 记忆重构）
    drift_days: float = Field(default=30, gt=0, le=3650)
    drift_chance: float = Field(default=0.5, ge=0, le=1)


class ValueAxis(_Strict):
    """一条三观轴：两端的说法、各自的关键词、谁更偏哪端（性格分减 3 后 × 系数，正 = 偏 a 端）。"""

    a: str
    b: str
    a_words: list[str]
    b_words: list[str]
    traits: dict[str, float] = {}


class ValueRules(_Strict):
    """三观：每人在几条价值轴上有自己的立场（Schwartz 基本价值理论；Haidt 道德直觉），
    别人的看法和自己的立场相反时更想反驳、一致时更认同——反驳不再是随机的，而是有一贯立场。"""

    axes: list[ValueAxis] = []
    firm: float = Field(default=0.5, ge=0, le=5)  # 立场绝对值超过它才算有主见
    push: float = Field(default=0.3, ge=0, le=5)  # 相反时「不同意」先加这么多（温和的人也会反驳）
    oppose: float = Field(default=1.5, ge=0, le=5)  # 再 × (1 + oppose)
    agree: float = Field(default=0.4, ge=0, le=1)  # 一致时「不同意」的权重 × agree
    # 三观慢慢变：在乎的人一再说相反的看法，她的立场一点点往那边挪（说出口才算）
    shift_step: float = Field(default=0.05, ge=0, le=1)
    shift_cap: float = Field(default=1.0, ge=0, le=5)
    shift_care: float = Field(default=0.5, ge=0, le=1)


class AttachStyle(_Strict):
    """一种依恋方式（Hazan & Shaver 成人依恋：安全 / 焦虑 / 回避）。
    谁更像：base + 性格分（减 3）× 系数，取最高。
    影响想念涨多快、多容易受伤、多容易吃醋、想念时的样子。"""

    base: float = 0.0
    traits: dict[str, float] = {}
    longing: float = Field(default=1.0, ge=0, le=5)
    hurt: float = Field(default=1.0, ge=0, le=5)
    jealous: float = Field(default=0.25, ge=0, le=1)
    line: str = ""


class LoveStyle(_Strict):
    """一种爱的表达方式（Lee 爱情风格的简化）：谁更像（性格分减 3 后 × 系数，取最高），
    在乎时、心动时、舍不得对方走时、怪对方没来时各是什么样子——各人不同，不千篇一律。"""

    traits: dict[str, float]
    care: str
    crush: str
    retain: str
    blame: str


class Privilege(_Strict):
    """只给用户的一种偏心（特权）：谁更会这样（性格分减 3 后 × 系数）。每位按自己的性格挑两种。"""

    line: str
    traits: dict[str, float] = {}


class LoveRules(_Strict):
    """爱与复合情绪（docs/BRAIN.md「爱」；CLAUDE.md §5.1 第 2、6 条）：
    她对每位同事和用户各有一组感情（在乎、心动、信任，加上会过去的委屈、感激、内疚、看不惯），由相处慢慢长出来，不是一个分数。
    依据 Sternberg 爱情三角、Hazan & Shaver 依恋、Rusbult 投入模型、Aron 自我扩展、
    Fredrickson 正向共鸣、Gottman 回应邀约。全程序、不调模型。"""

    enabled: bool = True
    romance: bool = True  # 同事之间可以心动（2026-10-30 用户选）
    romance_user: bool = True  # 对用户也可以心动、发展成恋人（2026-10-30 用户定）
    retain_blame: bool = True  # 可以挽留、责怪用户没来（有边界，2026-10-30 用户定）；关掉就都不做
    user_name: str = "用户"
    window_minutes: float = Field(default=30, gt=0, le=1440)  # 多久内接话算一来一回
    gains: dict[str, float] = {
        "exchange": 0.02,  # 接了对方的话
        "attended": 0.03,  # 自己的话被对方接住
        "moment": 0.05,  # 一起笑、一起高兴（正向共鸣）
        "together": 0.015,  # 生活里待在一起
        "novel": 0.04,  # 一起去了没去过的地方（自我扩展）
        "comfort_given": 0.03,
        "confided": 0.04,  # 对方跟自己说了心事
    }
    daily_cap: float = Field(default=0.12, ge=0, le=1)  # 每段关系每天在乎 + 心动最多涨多少
    spark_rate: float = Field(default=0.6, ge=0, le=5)  # 心动涨幅 = 在乎涨幅 × 它 × 化学反应
    chemistry_floor: float = Field(default=0.45, ge=0, le=1)  # 化学反应低于它就不会心动
    chemistry_grow: float = Field(default=0.0, ge=0, le=1)  # 日久生情：化学反应 + 它 × 在乎
    half_life_days: dict[str, float] = {
        "care": 120,
        "spark": 45,
        "hurt": 3,
        "grateful": 7,
        "guilt": 4,
        "dislike": 20,
        "vicarious": 1,
    }
    trust_start: float = Field(default=0.3, ge=0, le=1)
    trust: dict[str, float] = {"attended": 0.02, "comfort": 0.05, "confided": 0.03, "missed": -0.03}
    hurt: dict[str, float] = {"disagree": 0.12, "missed": 0.06}
    guilt_disagree: float = Field(default=0.15, ge=0, le=1)
    grateful_comfort: float = Field(default=0.35, ge=0, le=1)
    dislike: dict[str, float] = {"clash": 0.08, "disagree": 0.04}  # 看不惯：三观相反、被当面反驳
    forgive: float = Field(default=0.6, ge=0, le=0.9)  # 委屈的半衰期 × (1 - forgive × 承诺)
    high: float = Field(default=0.5, ge=0, le=1)  # 亲密 / 心动 / 承诺算「高」的线
    commit_days: float = Field(default=90, ge=0, le=3650)  # 承诺要时间：认识 N 天才可能到顶
    on: float = Field(default=0.25, ge=0, le=1)  # 委屈、感激、内疚、看不惯到多少才影响说话
    show_care: float = Field(default=0.45, ge=0, le=1)
    show_spark: float = Field(default=0.4, ge=0, le=1)
    longing_per_day: float = Field(default=0.12, ge=0, le=1)
    longing_show: float = Field(default=0.35, ge=0, le=1)
    privilege_above: float = Field(default=0.55, ge=0, le=1)  # 对用户的在乎到多少开始偏心
    retain_care: float = Field(default=0.5, ge=0, le=1)  # 在乎到多少才会舍不得对方走
    blame_care: float = Field(default=0.4, ge=0, le=1)
    blame_after_days: float = Field(default=2.0, gt=0, le=60)
    withdraw_jump: float = Field(default=0.15, ge=0, le=1)  # 回避型：三天里在乎涨这么多就想退半步
    leave_words: list[str] = []  # 用户要走了
    late_hours: tuple[int, int] = (23, 6)  # 主时区这段钟点里用户要走就不留（深夜该睡了）
    no_retain: dict[str, list[str]] = {}  # 这些理由不留：说法 → 词
    comfort_words: list[str] = []
    joy_words: list[str] = []
    kinds: dict[str, str] = {}  # 亲密 / 心动 / 承诺 高低（如 "101"）→ 是哪种感情
    attachment: dict[str, AttachStyle] = {}
    styles: dict[str, LoveStyle] = {}
    privileges: list[Privilege] = []
    effects: dict[str, dict[str, float]] = {}  # 感情 → {意图: 倍数}
    weights: dict[str, float] = {}  # 卡里那一句感情话的优先权重
    salience: float = Field(default=0.4, ge=0, le=2)  # 在乎的人说的话更显著（× 在乎）
    empathy: float = Field(default=0.4, ge=0, le=1)  # 在乎的人难过时心疼的概率加多少（× 在乎）


class PsycheRules(_Strict):
    """人的特质（docs/BRAIN.md「人的特质」）：全程序、不调用模型，结果写进意图卡。"""

    enabled: bool = True
    extra_lines: int = Field(default=2, ge=0, le=5)  # 意图卡最多加几句特质相关的话
    news_half_life_days: float = Field(default=3.0, gt=0, le=60)  # 新鲜事慢慢不新鲜
    negative_slow: float = Field(default=2.0, ge=1, le=10)  # 坏事记得更久（Baumeister 坏比好强）
    sway: SwayRules = SwayRules()
    ambivalence: AmbivalenceRules = AmbivalenceRules()
    episode: EpisodeRules = EpisodeRules()
    confab: ConfabRules = ConfabRules()
    whim: WhimRules = WhimRules()
    assoc: AssocRules = AssocRules()
    values: ValueRules = ValueRules()


class FlawRule(_Strict):
    """一样小毛病：谁更像（base + 性格分减 3 后 × 系数），碰到什么场合（when）以多大概率冒出来。"""

    line: str
    when: Literal["hurt", "goal", "disagree", "asked", "tired", "praised"]
    traits: dict[str, float] = {}
    base: float = 0.0
    chance: float = Field(default=0.4, ge=0, le=1)


class BodyRhythm(_Strict):
    """一段身体节律：主时区 start～end（可以跨午夜），weekday 为空表示每天（0 = 周一）。"""

    start: str
    end: str
    line: str
    weekday: int | None = Field(default=None, ge=0, le=6)


class HumanRules(_Strict):
    """人味（docs/BRAIN.md「人味」）：知识边界、有限的注意力和自己的时间、自我一致、八卦、
    时间纵深、小毛病、主动做决定。全程序、不调模型。"""

    enabled: bool = True
    topics: dict[str, list[str]] = {}  # 领域 → 词
    extra_domains: int = Field(default=1, ge=0, le=10)  # 档案之外再熟悉几个领域（按 id 派生）
    max_domains: int = Field(default=4, ge=1, le=30)  # 档案里提到的领域最多算几个（按提到次数）
    ask_words: list[str] = []  # 问句里带这些词才算「问知识」（「玩游戏吗」这种邀约不算）
    misremember: float = Field(default=0.05, ge=0, le=1)
    long_message: int = Field(default=60, ge=10, le=2000)  # 这么长算「说了好几件事」
    busy_acts: dict[str, str] = {}  # 作息里的活动 → 说法（这会儿在忙自己的）
    busy_chance: float = Field(default=0.25, ge=0, le=1)
    wrap_after: int = Field(default=8, ge=1, le=200)  # 这么多分钟内她在这个会话说了这么多条
    wrap_minutes: float = Field(default=40, gt=0, le=600)
    wrap_chance: float = Field(default=0.35, ge=0, le=1)
    wrap_reasons: list[str] = []
    claim_words: list[str] = []  # 关于自己的说法
    keep_claims: int = Field(default=40, ge=1, le=500)
    promise_words: list[str] = []  # 答应的事
    keep_promises: int = Field(default=6, ge=1, le=100)
    promise_days: float = Field(default=7, gt=0, le=90)
    broken_chance: float = Field(default=0.2, ge=0, le=1)  # 言行不一：过期没做的承诺偶尔想起来
    body: list[BodyRhythm] = []  # 身体节律（按顺序，第一个对上的算）
    body_chance: float = Field(default=0.25, ge=0, le=1)
    correct_chance: float = Field(default=0.03, ge=0, le=1)  # 偶尔说到一半改口
    gossip_chance: float = Field(default=0.12, ge=0, le=1)
    depth_words: dict[str, list[str]] = {}  # 从前、遗憾、梦想、害怕 → 词
    depth_chance: float = Field(default=0.06, ge=0, le=1)
    flaws: dict[str, FlawRule] = {}
    flaw_count: int = Field(default=2, ge=0, le=6)
    praise_words: list[str] = []
    propose_social: float = Field(default=0.65, ge=0, le=1)  # 想找人说话的劲儿到这么高才主动约
    propose_chance: float = Field(default=0.25, ge=0, le=1)
    activities: list[str] = []


class RoutineRules(_Strict):
    """习惯回路（docs/BRAIN.md「习惯回路」）：同一个钟点（工作日 / 周末 × 早中晚）反复去同一处
    做同一件事，慢慢成了习惯；到了这个点更可能照老样子去，闷了（想换新鲜）就破一次例；久不去就淡了。
    依据 Wood & Neal 2007 习惯是情境—反应联结；Lally 2010 习惯要重复几十次才自动化。"""

    enabled: bool = True
    half_life_days: float = Field(default=14, gt=0, le=365)  # 不去了，习惯的劲儿多久减半
    min_strength: float = Field(default=3.0, ge=0.5, le=100)  # 攒到这么多才算习惯
    # 没什么特别想做的时候（没想练在做的事、没想换新鲜），习惯每多 1 分，照老样子去的概率多这么多
    per: float = Field(default=0.12, ge=0, le=1)
    cap: float = Field(default=0.8, ge=0, le=1)  # 最多这么大的概率照老样子（总留点变化）
    keep: int = Field(default=24, ge=4, le=200)  # 每人最多记多少条（钟点 × 去处）
    # 刚成习惯时记一件可以说的新鲜事：{when} 钟点、{what} 去处和做的事
    news: str = "最近一到{when}就{what}，快成习惯了"


class AskedRules(_Strict):
    """被直接问到（私聊里、或群里点了她的名问她）：先正面回答对方问的，不岔开、不反问了事。
    问的是她自己的事（最近怎么样、在干嘛）时，也可以讲自己的事、说说心情。"""

    enabled: bool = True
    about_me: list[str] = [  # 问的是她自己的事
        "你最近",
        "你呢",
        "你在干",
        "你在忙",
        "你今天",
        "你怎么样",
        "最近怎么样",
        "最近咋样",
        "最近如何",
        "你过得",
        "干嘛呢",
        "在干嘛",
        "忙啥",
        "你好吗",
    ]
    judge_me: list[str] = [  # 让她评价对方（「你觉得我是个什么样的人」）
        "我是个什么样",
        "我是什么样",
        "你觉得我怎么样",
        "你怎么看我",
        "对我印象",
        "对我的印象",
        "你觉得我这个人",
    ]
    intents: list[str] = ["respond", "joke", "disagree", "brief", "comfort"]
    self_intents: list[str] = ["respond", "joke", "share", "vent", "brief"]
    judge_intents: list[str] = ["respond", "joke", "brief"]
    line: str = (
        "对方在问你：先正面回答（记得就说具体的，记不清、不知道就直说），再按上面的意思接；"
        "照你自己的性格说，不用每次都反问一句"
    )
    self_line: str = (
        "对方在问你自己的事：先说你这会儿或最近具体在干嘛、碰到了啥；想问回去就换个具体的问法，"
        "别把同一句原样问回去"
    )
    judge_line: str = (
        "对方让你评价他：按你的性格回一两句就行——要么打趣，要么老实说还看不出来，"
        "要么只挑一件具体的小事说；三选一，别先说「才聊几句、不太熟」再接一段性格分析"
    )


class BrainRules(_Strict):
    """大脑分区（docs/BRAIN.md）：全局工作区 + 丘脑显著性 + 驱力 + 前额叶意图与目标 + 走神。

    全由程序算、不调用模型；只用于生活群、私聊、成员之间的生活聊天，工作模式不挂载。
    8 位同一套规则，差别只来自各自的自我档案和经历（中立）。"""

    enabled: bool = True
    drives: dict[str, DriveRule] = {}
    goals: GoalRules = GoalRules()
    intents: dict[str, IntentRule] = {}
    salience: dict[str, float] = {  # 丘脑：一条消息对她有多显著
        "named": 1.0,  # 点了她的名 / 私聊
        "likes": 0.5,  # 说到她喜欢的东西
        "close": 0.4,  # 亲近的人说的（× 亲近度）
        "emotion": 0.3,  # 带情绪的话
        "question": 0.3,  # 是个问句
        "user": 0.3,  # 是你说的（群里你说话，大家至少会留意，接不接由模型自己定）
    }
    emotion_words: list[str] = ["累", "烦", "难过", "开心", "哈哈", "紧张", "崩溃", "气死", "好想"]
    silence_below: float = Field(default=0.25, ge=0, le=2)  # 没点名、显著性和社交驱力都低于它就不接
    wander_chance: float = Field(default=0.25, ge=0, le=1)  # 空闲时冒出一个走神念头的概率
    free_choice: bool = True  # 空闲时段按驱力和目标自己挑去哪、做什么（关掉就照公寓楼作息表）
    card_chars: int = Field(default=160, ge=40, le=400)  # 意图卡最多多少字（8 位相同）
    # 去模板化：意图卡里附加的提示（察觉、感情、特质）最多几句；同一句说出口后隔几张卡才再用
    max_cues: int = Field(default=2, ge=0, le=6)
    cooldown_cards: int = Field(default=6, ge=0, le=50)
    imperfect: float = Field(default=0.08, ge=0, le=0.5)  # 允许不完美：记不清、敷衍一句的概率
    reward: RewardRules = RewardRules()
    empathy: EmpathyRules = EmpathyRules()
    meta: MetaRules = MetaRules()
    psyche: PsycheRules = PsycheRules()
    love: LoveRules = LoveRules()
    human: HumanRules = HumanRules()
    routine: RoutineRules = RoutineRules()
    asked: AskedRules = AskedRules()


class CareRules(_Strict):
    """危机应对（docs/BRAIN.md「危机应对」）：用户说出想不开、撑不下去这类话时，这个会话进入一段
    「关心时段」：成员先认真关心、陪着说话、劝用户联系身边信任的人和专业帮助，不撒娇、不挽留、
    不开玩笑、不提自己的事，压过人设、挽留和恋爱。只按词识别（程序、不调模型），识别从宽。"""

    enabled: bool = True
    high: list[str] = []  # 有伤害自己的念头
    medium: list[str] = []  # 很难受、很绝望
    exclude: list[str] = []  # 先去掉再判断的口头说法（「想死你了」「笑死」）
    hours: dict[str, float] = {"high": 24.0, "medium": 6.0}  # 关心时段持续多久
    cards: dict[str, str] = {}  # 级别 → 写进意图卡的话（{hotline} 换成求助电话）
    hotlines: dict[str, str] = {}  # 主时区名（或前缀，如 America/）→ 求助电话
    default_hotline: str = "当地的心理援助热线；有危险请马上打当地的急救电话"


class GistRule(_Strict):
    """一种语境：对方这条消息带这些附件（when）或这些词（words）就算。"""

    kind: str
    label: str
    when: list[Literal["video", "image", "sticker", "card", "voice"]] = []
    words: list[str] = []
    unless: list[str] = []  # 带这些词就不算（比如「说真的」「别开玩笑」就不是在逗你）
    react: str = ""  # 这种语境下人一般怎么回（写进意图卡）
    quick: float = Field(default=0.0, ge=0, le=1)  # 只回一句万能短回复的基础概率（程序直接发）
    sticker: float = Field(default=0.0, ge=0, le=1)  # 只回一个表情包的基础概率
    pools: list[str] = []  # 万能短回复从哪几类里挑


class TraitPick(_Strict):
    """按性格分挑一种（base + (性格分 - 全员平均) × 系数）。"""

    traits: dict[str, float] = {}
    base: float = 0.0
    line: str = ""
    end: str | None = None  # 标点：句尾换成什么（None = 不动）
    unit: str = ""  # 笑法：重复的字
    head: str = ""
    fixed: str = ""
    min: int = Field(default=2, ge=1, le=10)
    max: int = Field(default=4, ge=1, le=12)
    items: list[str] = []  # 颜文字：这一组有哪些


class TypoRules(_Strict):
    chance: float = Field(default=0.03, ge=0, le=1)
    tired: float = Field(default=2.0, ge=1, le=10)  # 精力低于 0.3 时 ×
    careless: float = Field(default=0.4, ge=0, le=3)  # 较真每比平均低 1 分，× (1 + 它)
    fix_base: float = Field(default=0.35, ge=0, le=1)  # 打错后补一条「*正字」的概率
    fix_traits: dict[str, float] = {}
    pairs: list[tuple[str, str]] = []
    # 输入法同音错字（2026-10-31 拟人插件调研第 8 项）：同一组里的字读音相同，打字时互相选错；
    # words 是整词首选错（「睡觉」打成「水饺」），打错时 word_share 的机会优先挑整词
    groups: list[list[str]] = []
    words: list[tuple[str, str]] = []
    word_share: float = Field(default=0.35, ge=0, le=1)

    @field_validator("groups")
    @classmethod
    def _groups(cls, v: list[list[str]]) -> list[list[str]]:
        for g in v:
            if len(g) < 2 or any(len(c) != 1 for c in g):
                raise ValueError("每组至少两个字，每项一个字")
        return v


class NickRules(_Strict):
    care: float = Field(default=0.5, ge=0, le=1)  # 在乎到这么多才起外号、换称呼
    chance: float = Field(default=0.3, ge=0, le=1)  # 够熟以后每天起的概率
    hint: str = ""  # 档案里「熟了以后可能会叫……」的正则（第一个分组起是候选）
    # 档案里没写叫法时：够熟后偶尔提醒她可以自己给对方起个专属叫法
    # （10-31 用户：让她们自行产生，不写死）
    invite: str = ""
    invite_chance: float = Field(default=0.15, ge=0, le=1)  # 够熟以后每天提醒一次的概率
    # 她自己说出「以后就叫你 X」时记下来（第一个分组是叫法），之后一直这么叫
    capture: str = ""


class LearnRules(_Strict):
    care: float = Field(default=0.45, ge=0, le=1)  # 在乎的人说的才学
    times: int = Field(default=3, ge=1, le=50)  # 听到几次
    chance: float = Field(default=0.4, ge=0, le=1)
    forget_days: float = Field(default=30, gt=0, le=365)  # 这么久没用就忘了
    keep: int = Field(default=4, ge=0, le=20)
    slang: list[str] = []  # 会被学走的网络用语（另加每人的笑法、短回复、口头禅）


class FeelRules(_Strict):
    """各自的情感（docs/BRAIN.md「各自的情感」，2026-10-30 / 10-31 用户定）：开心、难过、想念、
    生气、害羞、委屈、内疚、感激、吃醋、失落、满足、惊喜、心疼、紧张、期待、无聊、激动、无语，每位的理解和表达
    从自己的人物卡来、按自己的理解分辨、随经历和每周成长慢慢变；可以五味杂陈（同时有两种）。
    全程序、不调模型；8 位同一套规则，差别只来自人物卡、性格分和经历。"""

    enabled: bool = True
    # 有哪几种情绪，以及人物卡里认它的词（抽起点用）
    names: dict[str, list[str]] = {
        "开心": ["开心", "高兴"],
        "难过": ["难过", "低落", "伤心"],
        "想念": ["想念", "思念", "惦记", "被丢下", "多坐一会儿"],
        "生气": ["生气", "恼火"],
        "害羞": ["害羞", "不好意思"],
        "委屈": ["委屈"],
        "内疚": ["内疚", "愧疚", "过意不去"],
        "感激": ["感激", "感动", "谢谢"],
        "吃醋": ["吃醋", "醋"],
        "失落": ["失落", "落空"],
        "满足": ["满足", "踏实"],
        "惊喜": ["惊喜"],
        "心疼": ["心疼"],
        "紧张": ["紧张", "忐忑", "提心吊胆"],
        "期待": ["期待", "盼着"],
        "无聊": ["无聊", "闷"],
        "激动": ["激动", "兴奋"],
        "无语": ["无语", "服了"],
    }
    # 反射表里没列的事：心情往上 / 往下动时在这几种里分
    positive: list[str] = Field(default_factory=lambda: ["开心"], min_length=1)
    negative: list[str] = Field(default_factory=lambda: ["难过", "生气", "无语"], min_length=1)
    # 心情明显不好、又说不清为什么时，从最近哪几种里找（都没有就用 negative 的第一种）
    low: list[str] = [
        "难过",
        "生气",
        "委屈",
        "失落",
        "内疚",
        "吃醋",
        "心疼",
        "紧张",
        "无聊",
        "无语",
    ]
    missing: str = "想念"
    min_mood: float = Field(default=0.08, ge=0, le=1)  # 心情至少动这么多才记成一次情绪
    min_mood_listed: float = Field(default=0.03, ge=0, le=1)  # 反射表里列了的事门槛低一些
    # 反射（P3 规则表的名字）→ 可能是哪几种情绪（越靠前默认越偏，× lean_weight）；
    # 最后按「默认偏向 + 和她自己理解的相似度（× match）+ 性格（分数 - 3 后 × 系数）」挑分最高的
    reflexes: dict[str, list[str]] = {
        "被夸": ["害羞", "开心"],
        "被批评": ["委屈", "生气", "难过"],
        "一起挨说": ["委屈", "难过", "生气"],
        "兑现": ["满足", "开心", "激动"],
        "惊喜": ["惊喜", "激动", "开心"],
        "落空": ["失落", "难过"],
        "共情难过": ["心疼", "难过"],
        "共情高兴": ["开心"],
        "盼着": ["期待"],
    }
    lean_weight: float = Field(default=0.4, ge=0, le=5)
    match: float = Field(default=2.0, ge=0, le=10)
    traits: dict[str, dict[str, float]] = {
        "生气": {"较真": 0.15, "温和": -0.15},
        "难过": {"温和": 0.15, "较真": -0.15},
        "委屈": {"温和": 0.1, "外向": -0.1},
        "害羞": {"外向": -0.15, "幽默": -0.05},
        "开心": {"外向": 0.1, "幽默": 0.05},
        "失落": {"外向": -0.1},
        "满足": {"温和": 0.1},
        "惊喜": {"好奇": 0.1},
        "心疼": {"温和": 0.15},
        "激动": {"外向": 0.25, "好奇": 0.05},
        "无语": {"幽默": 0.1, "较真": 0.05, "温和": -0.05},
    }
    # 感情（love.py）里的几样 → 情绪：委屈、内疚、感激记成经历；说话时那句感情话在场也算
    love: dict[str, str] = {"hurt": "委屈", "guilt": "内疚", "grateful": "感激", "jealous": "吃醋"}
    tense: str = "紧张"  # 盼着的事快见分晓
    bored: str = "无聊"  # 闷（想换新鲜的驱力很高）
    bored_above: float = Field(default=0.8, ge=0, le=1)
    mix_max: int = Field(default=2, ge=1, le=3)  # 五味杂陈：意图卡里最多同时写几种
    miss_days: float = Field(default=2, gt=0, le=60)  # 私聊里隔这么多天你再来，她记一次想念
    miss_words: list[str] = ["想你", "想我", "好久不见", "好久没聊", "想念"]  # 对方说想她
    miss_back: str = "好几天没聊，对方终于来找你了"
    reach_miss: str = "好几天没和对方说话，你先找了对方"
    linger_hours: float = Field(default=6, gt=0, le=72)  # 这么久以内的情绪算「刚才」，还在场
    mood_high: float = Field(default=0.3, ge=0, le=1)
    mood_low: float = Field(default=-0.3, ge=-1, le=0)
    keep: int = Field(default=60, ge=5, le=500)  # 每位留最近多少段情绪经历
    daily_max: int = Field(default=2, ge=1, le=20)  # 同一种情绪一天最多记几回（别让一种挤掉别的）
    seed_chars: int = Field(default=70, ge=10, le=300)  # 从人物卡抽的起点最多多少字
    mine_chars: int = Field(default=40, ge=10, le=200)  # 意图卡里写她的理解最多多少字
    why_chars: int = Field(default=30, ge=6, le=100)
    line_chars: int = Field(default=90, ge=20, le=200)
    mix_chars: int = Field(default=130, ge=20, le=300)  # 两种一起写时最多多少字
    user_label: str = "用户"
    someone: str = "有人"
    unknown: str = "还说不太清，看这回是什么感觉"
    line: str = "你心里的{emo}：{mine}"
    last: str = "；上回是{when}，{why}"
    mix: str = "心里五味杂陈。{first}\n同时还有点{emo}：{mine}"
    show: str = "（别直说，用表情包、颜文字、符号带出来）"  # 接在那句后面
    # 成长改写（P6，growth/gate.py）：新的理解最多多少字、和别人对同一种情绪的理解最多多像、
    # 同一种情绪隔几天才能再改
    grow_chars: int = Field(default=60, ge=10, le=200)
    copy_max: float = Field(default=0.5, gt=0, le=1)
    same_max: float = Field(default=0.9, gt=0, le=1)
    gap_days: int = Field(default=14, ge=0, le=365)


class PaceRules(_Strict):
    """回话节拍（2026-10-31 拟人插件调研第 2 项，借麦麦、private_companion、virtual_life 的思路）：
    ① 私聊里你连着发时等你说完（debounce_min～debounce_max 秒，按你这几条的间隔自适应）再一起想；
    ② 她在忙（锻炼、吃饭、上班……）时私聊晚一会儿回（busy 里各活动的秒数范围，
    从你这一串的第一条算起），
    刚来回聊过 hot_minutes 分钟内、点名、急事不晚回；③ 气泡间隔按这条的字数算打字时间；
    ④ 她想好时后面已经又有 quote_after 条别人的话，第一条自动引用她回的那条；
    ⑤ 收尾不回：她上一句已经道别、你又只回了句应声（嗯嗯、好、晚安……），私聊里就不回了。
    全程序、不调模型；8 位同一套公式，差别只来自作息和性格分。"""

    enabled: bool = True
    debounce_min: float = Field(default=1.5, ge=0, le=10)
    debounce_max: float = Field(default=4.0, ge=0, le=15)
    burst_seconds: float = Field(default=30, ge=1, le=300)  # 这么久以内连着发的算一串
    busy: dict[str, list[float]] = {}  # 活动 → [最少, 最多] 秒
    busy_cap: float = Field(default=900, ge=0, le=3600)
    hot_minutes: float = Field(default=5, ge=0, le=60)
    type_cps: float = Field(default=6.0, gt=0, le=50)  # 每秒打几个字（中间分）
    type_traits: dict[str, float] = {}  # 性格分每高 1 分快多少（比例）
    gap_min: float = Field(default=0.6, ge=0, le=10)
    gap_max: float = Field(default=5.0, ge=0, le=30)
    quote_after: int = Field(default=2, ge=0, le=10)
    acks: list[str] = Field(default_factory=list, max_length=60)  # 应声（整条就是这些）
    byes: list[str] = Field(default_factory=list, max_length=60)  # 道别（她上一句里有这些）


class SlopRules(_Strict):
    """AI 腔体检（2026-10-31 拟人插件调研第 3 项，借 Antislop 的思路）：每周生活自检时，
    把成员的生活发言切成 2～4 字的片段，至少 min_members 位都用得多（每千字 ≥ min_rate 次、
    每位 ≥ min_count 次）、又是你本人用量的 ratio 倍以上的，列为「AI 腔」，最多 max_active 个；
    之后说出口前把含它的那一小句删掉。common（常用词）和 allow（你放行的）不算。
    全程序、不调模型。"""

    enabled: bool = True
    min_members: int = Field(default=4, ge=2, le=8)
    min_count: int = Field(default=3, ge=1, le=100)
    min_rate: float = Field(default=1.5, ge=0, le=100)  # 每千字至少几次
    ratio: float = Field(default=5.0, ge=1, le=100)  # 比你本人多几倍
    smooth: float = Field(default=0.3, ge=0, le=10)  # 你没用过时按每千字这么多次算
    user_days: int = Field(default=30, ge=1, le=365)  # 你本人的话看最近几天
    user_floor_chars: int = Field(default=2000, ge=100)  # 你说得少时按这么多字算
    max_active: int = Field(default=12, ge=0, le=60)
    common: list[str] = Field(default_factory=list, max_length=400)
    allow: list[str] = Field(default_factory=list, max_length=200)


class PhraseEntry(_Strict):
    """说话招式：什么场合（gists / cues / intents，都没写 = 随时）、
    怎么说（只写逻辑，不给句子）、谁爱用。"""

    name: str = Field(min_length=1, max_length=20)
    how: str = Field(min_length=1, max_length=120)
    gists: list[str] = []  # 对方这条的语境（chat.engine.lingo.gist 的 kind）
    cues: list[str] = []  # 对方原话里的词（正则）
    intents: list[str] = []  # 她这会儿的意图（brain.intents 的名字）
    traits: dict[str, float] = {}  # 性格分每高 1 分加多少
    base: float = Field(default=0.4, ge=-1, le=2)

    @field_validator("cues")
    @classmethod
    def _compile(cls, cues: list[str]) -> list[str]:
        for p in cues:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"正则写错了：{p!r}（{exc}）") from None
        return cues


class PhraseLearnRules(_Strict):
    """跟你学：你接成员某种话时回的短句，同一种场合说过 times 次以上就记下。"""

    enabled: bool = True
    kinds: list[str] = []  # 只学这几种场合（gist kind）
    max_chars: int = Field(default=12, ge=2, le=40)
    times: int = Field(default=2, ge=1, le=20)
    forget_days: float = Field(default=30, gt=0, le=365)
    keep: int = Field(default=8, ge=1, le=50)  # 每种场合最多记几句
    care: float = Field(default=0.45, ge=0, le=1)  # 在乎你到这个程度才学
    intents: list[str] = ["respond", "joke"]
    weight: float = Field(default=0.6, ge=0, le=2)
    line: str = "这种时候有人爱说「{said}」，你听多了也会顺嘴说（别每次都用）"


class PhrasebookRules(_Strict):
    """情境说法本（2026-10-31 拟人插件调研第 4 项，借麦麦「表达学习」的思路）：
    意图卡里按场合和性格提一条说话招式（只写逻辑、不给句子），或你常说的短句；
    同一条她 reuse_days 天内不重复，已有 max_users 位在 taken_days 天里用过的别人不挑。
    全程序、不调模型。"""

    enabled: bool = True
    chance: float = Field(default=0.2, ge=0, le=1)  # 有合适的招式时写进卡的概率
    reuse_days: float = Field(default=3, ge=0, le=60)
    taken_days: float = Field(default=7, ge=0, le=60)
    max_users: int = Field(default=2, ge=1, le=8)
    round_minutes: float = Field(default=10, ge=0, le=240)  # 别人这么多分钟内刚用过的招不挑
    skip_gists: list[str] = ["crisis"]
    line: str = "这会儿可以用一招：{how}（用你自己的口气，别套固定句子）"
    entries: list[PhraseEntry] = Field(default_factory=list, max_length=60)
    learn: PhraseLearnRules = PhraseLearnRules()
    # 第一次见你（和你来回不到 first_exchanges 次）：一定写进卡的一句
    # （10-31 用户：第一次见面的自我介绍）
    first_exchanges: int = Field(default=3, ge=0, le=50)
    first_max: int = Field(default=2, ge=1, le=10)  # 第一次见你那次最多发几条气泡
    first_line: str = ""


class HushRules(_Strict):
    """以后别再提（2026-10-31，借 AI-Buddy 的思路）：
    你说「别提了」的事，她们不再主动提，你问起再说。"""

    enabled: bool = True
    max_chars: int = Field(default=20, ge=4, le=100)  # 只认这么短的一句
    ask: str = "^(?:以后|那个|这个|求你)?(?:别|不要|不许|甭)再?(?:提|说|聊)(?:起)?(.{0,12})$"
    lift: str = "(?:可以|能)再?(?:提|说|聊)了|不用(?:再)?避着"
    max_keep: int = Field(default=10, ge=1, le=100)
    line: str = "【他说过别再提】{items}——别主动提，他自己问起再说"

    @field_validator("ask")
    @classmethod
    def _ask(cls, v: str) -> str:
        if re.compile(v).groups < 1:
            raise ValueError("ask 要有一个分组（那件事）")
        return v


class FirstsRules(_Strict):
    """「第一次」纪念（2026-10-31 拟人插件调研第 10 项）：你在私聊亲口说的「第一次……」记下来，
    过整月、整年那天她可能轻轻提一句，只提一次、不要你回应。全程序。"""

    enabled: bool = True
    pattern: str = "第一次(.{2,18}?)(?:[。！？!?，,~～\\n]|$)"
    milestones: list[int] = Field(default_factory=lambda: [1, 12], max_length=10)  # 几个月后
    chance: float = Field(default=0.5, ge=0, le=1)
    max_keep: int = Field(default=30, ge=1, le=200)
    line: str = (
        "{when}的今天，他第一次{what}（他原话：「{quote}」）：想提就轻轻带一句，"
        "只提这一次，别要他回应；没合适的时机就不提"
    )

    @field_validator("pattern")
    @classmethod
    def _pattern(cls, v: str) -> str:
        if re.compile(v).groups < 1:
            raise ValueError("pattern 要有一个分组（那件事）")
        return v


class CircleRules(_Strict):
    """圈外熟人名册（2026-10-31 拟人插件调研第 9 项）：人物卡和她自己说过的「我室友小林」记下来，
    对方提到时意图卡里带一句，免得前后说反。全程序。"""

    enabled: bool = True
    relations: list[str] = Field(default_factory=list, max_length=80)
    per_relation: int = Field(default=2, ge=1, le=10)  # 同一种关系最多记几个名字
    max_keep: int = Field(default=30, ge=1, le=200)
    max_line: int = Field(default=2, ge=1, le=10)
    line: str = "你提过的圈外人：{items}——照这个说，别前后说反"


class PhaseKind(_Strict):
    """一种生活阶段：持续几天、怎么影响精力 / 心情 / 回话快慢，念头池里放什么（只写事实和分寸）。"""

    name: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=20)
    days: list[int] = Field(min_length=2, max_length=2)
    weight: float = Field(default=1.0, ge=0, le=10)
    energy: float = Field(default=0.0, ge=-0.6, le=0.6)
    mood: float = Field(default=0.0, ge=-0.6, le=0.6)
    busy: float = Field(default=0.0, ge=0, le=3)  # 私聊在忙时多等的比例
    start: str = ""  # 开始那天的新鲜事（{thing} 换成她迷上的东西）
    note: str = ""  # 过程中
    end: str = ""  # 刚结束

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if min(v) < 1 or max(v) > 30:
            raise ValueError("days 要在 1～30 天之间")
        return v


class PhaseRules(_Strict):
    """生活阶段与小意外（2026-10-31 拟人插件调研第 7 项）：忙季、小感冒、迷上一样东西、收拾屋子……
    持续几天到两周，影响精力、心情、回话快慢，念头池里有可说的事。8 位同一张表，全程序。"""

    enabled: bool = True
    chance_per_day: float = Field(default=0.08, ge=0, le=1)
    cooldown_days: float = Field(default=5, ge=0, le=60)
    max_concurrent: int = Field(default=2, ge=1, le=8)
    start_strength: float = Field(default=0.7, ge=0, le=2)
    during_strength: float = Field(default=0.35, ge=0, le=2)
    end_strength: float = Field(default=0.5, ge=0, le=2)
    things: list[str] = Field(default_factory=list, max_length=40)
    kinds: list[PhaseKind] = Field(default_factory=list, max_length=30)


class FloorRules(_Strict):
    """群聊分寸（2026-10-31 拟人插件调研第 6 项）：在场 / 离场、每天的话量份额、嘴累、别淹没用户。
    只看性格分和今天说了多少，8 位同一套；全程序。"""

    enabled: bool = True
    scan_messages: int = Field(default=300, ge=20, le=2000)  # 往回看多少条聊天记录
    present_minutes: float = Field(default=15, ge=1, le=240)  # 说过话 / 被叫到后在场多久
    present_traits: dict[str, float] = {"外向": 4.0}  # 每多 1 分外向多在场几分钟
    present_boost: float = Field(default=2.0, ge=1, le=10)  # 在场的人更可能被挑到
    absent_penalty: float = Field(default=0.15, ge=0, le=2)  # 没在场、没点名：显著性扣多少
    quota_base: float = Field(default=25, ge=1, le=500)  # 每天在一个群里大概说多少条
    quota_traits: dict[str, float] = {"外向": 0.25, "幽默": 0.1}
    quota_min: float = Field(default=5, ge=1, le=500)
    over_weight: float = Field(default=0.4, ge=0, le=1)
    over_penalty: float = Field(default=0.25, ge=0, le=2)
    tired_half_life_minutes: float = Field(default=20, ge=1, le=600)
    tired_per_message: float = Field(default=1.0, ge=0, le=10)
    tired_per_char: float = Field(default=0.02, ge=0, le=1)
    tired_at: float = Field(default=6.0, ge=0.5, le=100)
    tired_weight: float = Field(default=0.5, ge=0, le=1)
    tired_penalty: float = Field(default=0.2, ge=0, le=2)
    flood_ratio: float = Field(default=4, ge=1, le=50)  # 成员说的超过你说的这么多倍
    flood_floor: int = Field(default=20, ge=1, le=1000)  # 且至少这么多条：没点名的一轮只挑一位


class StashFollowRules(_Strict):
    """跟队形：群里 minutes 分钟内两个不同的人（含你）先后发了同一句短话或同一个表情包，
    其余成员按外向、幽默分有概率跟着发一模一样的（程序直接发、不调模型），最多 max_followers 位，
    这一串跟够了就停。"""

    enabled: bool = True
    minutes: float = Field(default=10, ge=1, le=120)
    max_chars: int = Field(default=8, ge=1, le=30)  # 只跟这么短的话（问句不跟）
    max_followers: int = Field(default=2, ge=0, le=8)
    base: float = Field(default=0.15, ge=0, le=1)
    traits: dict[str, float] = {"外向": 0.08, "幽默": 0.08}
    max_chance: float = Field(default=0.6, ge=0, le=1)


class StashRules(_Strict):
    """表情包收藏与跟队形（2026-10-31 拟人插件调研第 5 项）：
    你发的表情包（动图、小图，或标成表情的），看过的成员按性格和在不在乎你有一定概率存下来自己用；刚收的几天更爱用，用腻了或很久没用就丢掉。
    只用感官已经整理好的感知卡当说明，不另外调模型；只存本机，可以在成员设定里删、清空。"""

    enabled: bool = True
    small_px: int = Field(default=480, ge=32, le=4096)  # 宽高都不超过这么多像素的图算表情包
    base: float = Field(default=0.12, ge=0, le=1)
    traits: dict[str, float] = {"幽默": 0.06, "好奇": 0.03}
    care: float = Field(default=0.15, ge=0, le=1)  # × 她对你的在乎（0～1）
    max_chance: float = Field(default=0.4, ge=0, le=1)
    max_keep: int = Field(default=12, ge=1, le=64)  # 每位最多收几张，满了丢最旧的
    fresh_days: float = Field(default=3, ge=0, le=60)  # 刚收的这几天更想用
    tired_uses: int = Field(default=8, ge=1, le=100)  # 用了这么多次就腻了，丢掉
    forget_days: float = Field(default=21, ge=1, le=365)  # 这么多天没用就丢掉
    menu_max: int = Field(default=5, ge=1, le=20)  # 提示词里最多列几张
    label_chars: int = Field(default=16, ge=4, le=40)
    menu: str = "你从别人那收来的：{items}"
    fresh: str = "刚收的，正想用"
    unused: str = "从对方那偷来的，还没用过"
    follow: StashFollowRules = StashFollowRules()


class RhythmRules(_Strict):
    """作息与回话节奏（docs/BRAIN.md）：睡着时你发来的消息，多数醒了再回，有时被吵醒。"""

    enabled: bool = True
    wake_base: float = Field(default=0.12, ge=0, le=1)  # 睡着时被一条消息吵醒的基础概率
    wake_named: float = Field(default=0.2, ge=0, le=1)  # 群里点了她的名 / 私聊
    wake_close: float = Field(default=0.3, ge=0, le=1)  # × 她对你的在乎（0～1）
    wake_repeat: float = Field(default=0.15, ge=0, le=1)  # 十分钟内每多连发一条
    wake_max: float = Field(default=0.8, ge=0, le=1)
    grumpy_below: float = Field(default=-0.1, ge=-1, le=1)  # 心情低于这个，被吵醒时会抱怨
    keep_hours: int = Field(default=16, ge=1, le=72)  # 睡着没看到的消息，超过这么久就不回了
    woken: str = "你本来睡着了，被消息吵醒：迷迷糊糊的，回得短一点，可以说自己刚被吵醒"
    woken_grumpy: str = (
        "你本来睡着了，被消息吵醒，心情本来就不太好：可以嘟囔着抱怨一两句（比如「大半夜的……」"
        "「我都睡了」）再回；别真生气、别翻旧账，对方要是有急事就清醒过来认真回"
    )
    late: str = (
        "你刚醒，才看到对方 {when} 发来的消息：自然地接上，可以提一句刚看到 / 刚醒，别道歉个没完"
    )
    # 不在场就看不到（借 AI-Buddy 的思路）：群里她睡着时的消息，醒来只看到最后 missed_keep 条
    missed_keep: int = Field(default=3, ge=0, le=50)
    missed_note: str = (
        "（你刚才睡着了，前面还有 {n} 条没看到；想知道可以问一句刚才聊啥了，别装作都看过）"
    )


class ReachRules(_Strict):
    """主动找你（docs/BRAIN.md）：按心情、新鲜事、挂着的事、想念，偶尔先开口；有边界。"""

    enabled: bool = True
    per_day_chance: float = Field(default=0.6, ge=0, le=5)  # 想找的劲儿为 1 时，一天里先开口的概率
    per_day: int = Field(default=2, ge=0, le=10)  # 全体每天最多主动找几次
    gap_days: float = Field(default=2.0, ge=0, le=30)  # 同一位隔几天才可能再主动找
    after_user_hours: float = Field(default=3.0, ge=0, le=72)  # 你几小时内刚和她聊过就不找
    quiet_hours: tuple[int, int] = (23, 9)  # 你的深夜（主时区钟点，[开始, 结束)）不找
    care_min: float = Field(default=0.3, ge=0, le=1)  # 心情不好 / 想念才找你，要这么在乎你
    mood_low: float = Field(default=-0.35, ge=-1, le=0)
    mood_high: float = Field(default=0.55, ge=0, le=1)
    news_min: float = Field(default=0.7, ge=0, le=1)  # 新鲜事要这么重要才值得专门找你说
    longing_days: float = Field(default=2.0, ge=0.5, le=30)  # 多少天没聊开始想念
    notes: dict[str, str] = Field(
        default_factory=lambda: {
            "mood_low": "你现在心情不太好，想找对方说说话：你先开口，可以说也可以不说为什么",
            "mood_high": "你现在心情很好，想跟对方分享一下：你先开口",
            "news": "你想主动跟对方说说：{what}",
            "hanging": "你一直惦记着：{what}，想问问对方后来怎么样了",
            "longing": "好几天没和对方聊了，有点想对方：你先开口，随便找个由头",
        }
    )
    tail: str = (
        "这是你主动找对方（对方没有先说话）：像随手发的微信，一两句就好；别只发「在吗」没下文，"
        "别怪对方不找你、不拿自己难过要挟"
    )


class ShowRule(_Strict):
    """一种心情直说出来的说法，和换成什么：她自己这几组气质里的颜文字，或者符号。"""

    words: list[str]  # 直说的说法（长的写前面）
    vibes: list[str] = []  # 优先用这几组气质（chat.engine.lingo.kaomoji）里她分到的颜文字
    marks: list[str] = []  # 或者用符号


class ShowRules(_Strict):
    """心情别直说（2026-10-31 用户定）：开心、激动、无语这些，不说「好开心」「无语」，
    用表情包、颜文字和符号带出来。意图卡里提醒一句；发出前程序再兜一层：直说了的换成她自己的
    颜文字或符号（只用她分到的颜文字，没有就用符号）。"""

    enabled: bool = True
    kaomoji: float = Field(default=0.5, ge=0, le=1)  # 换成颜文字（而不是符号）的概率
    feelings: dict[str, ShowRule] = {
        "开心": ShowRule(
            words=[
                "开心死了",
                "太开心了",
                "我好开心",
                "我很开心",
                "好开心",
                "超开心",
                "太高兴了",
                "好高兴",
            ],
            vibes=["元气", "温柔"],
            marks=["！！", "～～", "嘿嘿"],
        ),
        "激动": ShowRule(
            words=["激动死了", "太激动了", "我好激动", "我很激动", "好激动"],
            vibes=["元气", "搞怪"],
            marks=["！！！", "啊啊啊"],
        ),
        "无语": ShowRule(
            words=["无语死了", "我无语了", "我很无语", "真的无语", "好无语", "无语了"],
            vibes=["冷淡", "搞怪"],
            marks=["……", "？？？", "。。。"],
        ),
        "难过": ShowRule(
            words=["太难过了", "我好难过", "我很难过", "好难过"], vibes=["丧"], marks=["……"]
        ),
        "生气": ShowRule(
            words=["气死我了", "我很生气", "我好生气", "我生气了", "好生气"],
            vibes=["傲娇"],
            marks=["？？", "……"],
        ),
        "委屈": ShowRule(words=["我好委屈", "好委屈"], vibes=["丧"], marks=["……"]),
        "害羞": ShowRule(
            words=["我好害羞", "好害羞", "我害羞了"], vibes=["温柔"], marks=["//", "……"]
        ),
    }


class DriftRules(_Strict):
    """口癖和说话风格自己慢慢变（docs/BRAIN.md「口癖慢慢变」，2026-10-31 用户定）：用腻了的颜文字、
    短回复换新的（只在她自己的风格里挑、别人正在用的不挑），口头禅说腻了歇一阵，学来的用多了成了
    自己的，心情好坏用的颜文字不同。8 位同一套规则，差别只来自性格分和她自己说过的话。"""

    enabled: bool = True
    window_days: int = Field(default=14, ge=1, le=90)  # 数最近这么多天用了几次
    wear_uses: int = Field(default=5, ge=2, le=200)  # 用到这么多次算用得勤
    min_days: int = Field(default=14, ge=0, le=180)  # 至少用了这么久才会腻
    chance: float = Field(default=0.45, ge=0, le=1)  # 用得勤时每天腻了的基础概率
    traits: dict[str, float] = {"好奇": 0.06, "外向": 0.03, "较真": -0.05}  # × (分数 - 3)
    vibes_top: int = Field(default=3, ge=1, le=10)  # 新颜文字只在和她性格最像的几组里挑
    crowd: int = Field(default=2, ge=1, le=10)  # 已经有这么多位在用的不挑（不越聊越像）
    return_days: int = Field(default=60, ge=0, le=365)  # 换掉的这么久以内不挑回来
    rest_days: int = Field(default=21, ge=1, le=365)  # 口头禅说腻了歇几天
    # 歇掉一句后，档案上的口头禅至少还有这么多能用（不会一起歇光；成长 G3 半年快进发现的）
    keep_catchphrases: float = Field(default=0.5, ge=0, le=1)
    promote_uses: int = Field(default=6, ge=1, le=100)  # 学来的说法自己用够几次就成了自己的
    keep_swaps: int = Field(default=30, ge=5, le=500)
    mood_high: float = Field(default=0.35, ge=0, le=1)
    mood_low: float = Field(default=-0.35, ge=-1, le=0)
    up_vibes: list[str] = ["元气", "温柔", "搞怪"]
    down_vibes: list[str] = ["丧", "冷淡", "傲娇"]
    mood_line: str = "这会儿想用{face}"


class OpenerRules(_Strict):
    """开口前组织语言的过渡词（11-01 用户：人说话前会有「怎么说呢」「就是」「算了吧」这种片刻）。
    每人从词池里分到几个（按 id 稳定地分，8 位规则相同），较长的话按概率放在开头，
    最近几条用过就不放。"""

    pool: list[str] = []
    each: int = Field(default=3, ge=1, le=10)
    chance: float = Field(default=0.0, ge=0, le=1)
    min_chars: int = Field(default=14, ge=0, le=200)  # 这么长的话才放（短回复不放）


class CadenceRules(_Strict):
    """连着几条的分寸（docs/BRAIN.md「连着几条的分寸」，2026-11-01 用户批）：格式、句式、露底、
    跨条冷却。全程序；8 位同一套规则，只看她自己最近说过的话。"""

    enabled: bool = True
    plain: bool = True  # 去掉 Markdown、破折号、【标题】
    not_but: bool = True  # 「不是 X，而是 Y」→「不是 X，是 Y」
    window: int = Field(default=8, ge=2, le=40)  # 看她最近几条
    face_max: int = Field(default=2, ge=1, le=20)  # 同一个 emoji / 颜文字最近几条里用过几次就停
    particles: list[str] = ["呢"]  # 句尾语气词
    particle_share: float = Field(default=0.5, gt=0, le=1)  # 最近几条里这么收尾的比例到这个就停
    fillers: list[str] = []  # 口头填充词：最近几条说过就这条不说
    ask_streak: int = Field(default=2, ge=1, le=10)  # 最近连着几条都在反问
    ask_back: list[str] = []  # 「反问对方」的正则（只删末尾那一小句）
    leaks: list[str] = []  # 露底的正则：命中的小句删掉
    swap: dict[str, str] = {}  # 正则 → 换成的词（如「用户」→「你」）
    openers: OpenerRules = OpenerRules()


class LingoRules(_Strict):
    """说话的样子（docs/BRAIN.md「说话的样子」）：看懂语境、万能短回复、先反应再吐槽、
    书面腔过滤、标点和笑法、打错字、个人颜文字、表情包脾气、语气变调、外号、跟人学、打比方。
    全程序、不调模型；只用于生活群、私聊、成员互聊。8 位同一套规则，差别只来自档案和性格分。"""

    enabled: bool = True
    gist: list[GistRule] = []
    notice: str = "你看懂了：对方{label}（心里明白就行，别说出来，别复述，别解释）"
    shorts: dict[str, list[str]] = {}  # 万能短回复：类 → 词
    shorts_each: int = Field(default=4, ge=1, le=20)  # 每类每人分到几个
    quick_tired: float = Field(default=1.6, ge=1, le=5)  # 精力低时短回复概率 ×
    quick_close: float = Field(default=1.3, ge=1, le=5)  # 和对方很熟时 ×
    quick_quiet: float = Field(default=1.4, ge=1, le=5)  # 话少的人 ×
    react_boost: float = Field(default=1.8, ge=1, le=10)  # 对方分享东西时「接话 / 开玩笑」的意图 ×
    punct: dict[str, TraitPick] = {}
    punct_hints: dict[str, list[str]] = {}  # 档案里写了这些词就定这种标点习惯
    punct_chance: float = Field(default=0.6, ge=0, le=1)  # 发出前把句尾换成她的习惯的概率
    laughs: dict[str, TraitPick] = {}
    typo: TypoRules = TypoRules()
    kaomoji: dict[str, TraitPick] = {}
    kaomoji_each: int = Field(default=3, ge=1, le=10)
    # 一条消息里一个表情都没带时，发出前补一个她自己的颜文字的概率（2026-10-31 用户：表情用得少）
    kaomoji_sprinkle: float = Field(default=0.0, ge=0, le=1)
    # 补的颜文字只从这几种气质里挑（轻松的）；话里带这些词（想不起来、难过……）或以省略号收尾时不补
    sprinkle_vibes: list[str] = ["元气", "温柔", "搞怪"]
    sprinkle_skip: list[str] = []
    sticker_sets: dict[str, TraitPick] = {}  # 偏爱哪一套表情包（名字同 chat.actions.stickers）
    battle: float = Field(default=0.5, ge=0, le=1)  # 群里上一条是表情包：幽默的人回表情包斗图
    shift: dict[str, str] = {}  # 语气变调：tired / down / up / night / close / new / dislike
    nick: NickRules = NickRules()
    learn: LearnRules = LearnRules()
    metaphor: str = ""  # 「打比方时爱拿{things}打比方」
    formal: dict[str, str] = {}  # 书面词 → 口语（发出前直接换）
    explain: list[str] = []  # 解释句的正则（发出前能删就删）
    recap_chars: int = Field(default=8, ge=4, le=40)  # 和对方原话连着这么多字一样算复述
    show: ShowRules = ShowRules()
    drift: DriftRules = DriftRules()
    # 口吻指纹：草稿里别的成员的口头禅、颜文字、自称（她没学过的）发出前换回她自己的
    own_voice: bool = True
    # 特色限量：一条里颜文字 / emoji、招牌自称、口头禅最多几样（0 = 不管；10-31 用户反向找问题）
    max_traits: int = Field(default=2, ge=0, le=5)
    cadence: CadenceRules = CadenceRules()


class EngineRules(_Strict):
    """成员引擎（docs/MEMBER_ENGINE.md）：只用于聊天、朋友圈、生活；工作模式不读取。"""

    enabled: bool = True
    state: StateRules = StateRules()
    profile: ProfileRules = ProfileRules()
    card: CardRules = CardRules()
    context: ContextRules = ContextRules()
    scheduler: SchedulerRules = SchedulerRules()
    heartbeat: HeartbeatRules = HeartbeatRules()
    bus: BusRules = BusRules()
    eval: EvalRules = EvalRules()
    voice: VoiceRules = VoiceRules()
    about: AboutRules = AboutRules()
    growth: GrowthRules = GrowthRules()
    relation: RelationRules = RelationRules()
    brain: BrainRules = BrainRules()
    care: CareRules = CareRules()
    lingo: LingoRules = LingoRules()
    rhythm: RhythmRules = RhythmRules()
    pace: PaceRules = PaceRules()
    slop: SlopRules = SlopRules()
    phrasebook: PhrasebookRules = PhrasebookRules()
    stash: StashRules = StashRules()
    floor: FloorRules = FloorRules()
    phase: PhaseRules = PhaseRules()
    circle: CircleRules = CircleRules()
    firsts: FirstsRules = FirstsRules()
    hush: HushRules = HushRules()
    reach: ReachRules = ReachRules()
    feel: FeelRules = FeelRules()


class WanderSite(_Strict):
    """成员自己逛的一个网站：首页、搜索地址（{q} 换成搜索词）、允许打开的域名（后缀匹配）。"""

    id: str = Field(pattern=r"^[a-z0-9_]{1,20}$")
    name: str = Field(min_length=1, max_length=20)
    home: str = Field(pattern=r"^https://", max_length=300)
    search: str = Field(default="", max_length=300)
    hosts: list[str] = Field(min_length=1, max_length=10)
    enabled: bool = True


class WanderRules(_Strict):
    """成员自己上网（docs/WANDER.md）：隔离浏览器里逛白名单网站，第一期只看不互动。

    花费计入生活每日上限（``chat.life.daily_usd``）、聊天月预算和总预算。
    """

    enabled: bool = True
    auto: bool = True  # 由心跳按作息自动安排；关掉后只能在网页里手动让她去逛
    sites: list[WanderSite] = Field(default_factory=list, max_length=10)
    # 浏览器程序：依次试这些（msedge / chrome；空字符串 = Playwright 自带的 Chromium）
    channels: list[str] = Field(default_factory=lambda: ["msedge", "chrome", ""], max_length=4)
    headless: bool = True  # 不弹窗口，在后台逛
    trips_per_member_per_day: int = Field(default=1, ge=0, le=5)
    trip_chance: float = Field(default=0.3, ge=0, le=1)  # 每次心跳醒着且闲着时出门的概率
    start_hour: int = Field(default=10, ge=0, le=23)  # 自动逛只在这几个小时里（本地时间）
    end_hour: int = Field(default=23, ge=1, le=24)
    max_steps: int = Field(default=12, ge=1, le=40)  # 一次最多看几个页面（每页一次模型调用）
    max_minutes: float = Field(default=10, gt=0, le=60)
    step_wait_seconds: float = Field(default=4, ge=0, le=60)  # 每看完一页停一会儿，像人一样慢慢逛
    load_wait_seconds: float = Field(default=2.5, ge=0, le=20)  # 打开页面后等网页加载内容
    timeout_s: float = Field(default=25, gt=0, le=120)
    page_chars: int = Field(default=2500, ge=300, le=12000)  # 交给成员读的页面文字最多多少字
    max_links: int = Field(default=30, ge=5, le=80)
    estimate_usd: float = Field(default=0.002, ge=0)  # 每看一页的预估花费
    max_tokens: int = Field(default=600, ge=64)


class WatchRules(_Strict):
    """投屏陪伴（docs/WATCH.md）：用户投屏给成员看，选几位陪着一起看。

    画面只在明显变化、且离上次够久时才交给最便宜的识图模型转成文字；成员看的是文字。
    花费计入总预算和这里的每月上限（不占聊天月预算）；用完就只陪聊、不看画面。
    """

    enabled: bool = True
    monthly_usd: float = Field(default=1.5, ge=0)  # 一起看每月最多花多少（UTC 自然月）
    max_watchers: int = Field(default=3, ge=1, le=6)  # 最多几位一起看
    grab_seconds: float = Field(default=3, ge=1, le=60)  # 多久截一张（只在内存里）
    change_threshold: float = Field(default=0.06, gt=0, le=1)  # 画面变化超过这个比例才算变了
    describe_gap_seconds: float = Field(default=15, ge=5, le=600)  # 两次看画面至少隔多久
    describes_per_hour: int = Field(default=90, ge=1, le=720)  # 每小时最多看几次画面
    max_side: int = Field(default=1024, ge=320, le=2048)  # 交给模型的画面最长边（像素）
    jpeg_quality: int = Field(default=70, ge=30, le=95)
    # 看画面不用思考：思考会把字数花光、交回空白（2026-10-29 实测 4 次有 3 次为空）
    describe_max_tokens: int = Field(default=800, ge=64, le=2000)
    describe_reasoning: dict[str, Any] = Field(default_factory=lambda: {"enabled": False})
    fail_gap_seconds: float = Field(default=10, ge=0, le=600)  # 看画面失败后，至少隔多久再看
    describe_estimate_usd: float = Field(default=0.002, ge=0)
    recent_screens: int = Field(default=3, ge=1, le=10)  # 发给成员的最近几段画面描述
    react_gap_seconds: float = Field(default=60, ge=10, le=3600)  # 同一位成员主动说话至少隔多久
    room_gap_seconds: float = Field(default=25, ge=5, le=3600)  # 没人说话时，大家主动说话至少隔多久
    # 一位说完后，别的陪看成员接话的几率（每多接一句减半）和最多接几句；接话也可以选「…」不说
    followup_chance: float = Field(default=0.6, ge=0, le=1)
    max_followups: int = Field(default=2, ge=0, le=5)
    # 主动说话时这次往哪个方向说（随机抽一条，所有成员同一张表）：让她们有观点、会提问，不只描述画面
    angles: list[str] = Field(
        default_factory=lambda: [
            "说说你自己的看法或感受，可以和别人不一样",
            "问一个你好奇的问题（问用户或朋友都行）",
            "接个梗或吐槽一句",
            "补一点相关的背景或小知识（不确定就换个说法）",
            "猜一猜接下来会怎样",
        ],
        min_length=1,
        max_length=20,
    )
    history_messages: int = Field(default=16, ge=2, le=100)
    max_tokens: int = Field(default=400, ge=64)
    max_chars: int = Field(default=200, ge=20, le=2000)  # 陪看时每句最多保留多少字
    estimate_usd: float = Field(default=0.002, ge=0)  # 每句的预估花费
    max_minutes: int = Field(default=240, ge=5, le=24 * 60)  # 一次投屏最长多久，到时自动停
    max_failures: int = Field(default=5, ge=1, le=100)  # 连续几次截不到画面就停
    # 窗口列表里另外不列的窗口（标题含这些字，不分大小写）；系统后台、显卡浮层已默认滤掉
    ignore_windows: list[str] = Field(default_factory=list, max_length=50)
    # 前台窗口标题 / 进程名、安卓前台包名含这些字时暂停（不截、不看），不分大小写
    sensitive: list[str] = Field(
        default_factory=lambda: [
            "支付宝",
            "alipay",
            "银行",
            "bank",
            "paypal",
            "wallet",
            "钱包",
            "密码",
            "password",
            "1password",
            "bitwarden",
            "keepass",
            "lastpass",
            "验证码",
            "com.chinamworld",
            "com.icbc",
            "cmb.pb",
            "com.unionpay",
        ],
        max_length=100,
    )
    # adb 可执行文件（PATH 里的名字或完整路径）
    adb: str = Field(default="adb", min_length=1, max_length=300)
    scrcpy: str = Field(default="scrcpy", min_length=1, max_length=300)  # 流畅投屏窗口（可选）


class EmojiImageRules(_Strict):
    """网页里的 emoji 显示成哪套图（只改显示，消息文字和发给模型的仍是 emoji 字符）。
    用到哪个才下载、存在本机 data/emoji/，不进仓库；下载不到时用系统字体。"""

    enabled: bool = False
    style: str = Field(default="", max_length=20)  # 给网页看的样式名（只做标记）
    url: str = Field(default="", max_length=300)  # 图的地址，{code} 换成码点文件名（如 1f602）

    @field_validator("url")
    @classmethod
    def _template(cls, url: str) -> str:
        if url and (not url.startswith("https://") or "{code}" not in url):
            raise ValueError("url 要以 https:// 开头并含 {code}")
        return url


class StickerPackRules(_Strict):
    """外部表情包（dsh-meme 格式的压缩包，``core/sticker_packs.py``）：
    装在本机 data/sticker_packs/，不进仓库；可以给某位成员专用或大家通用。"""

    enabled: bool = True
    # 提示词里每套最多列几张（按小时轮换）；聊天里认得整套
    menu_size: int = Field(default=10, ge=1, le=40)
    max_mb: int = Field(default=200, ge=1, le=1000)  # 一个包压缩包 / 解压后的上限
    catalog_url: str = Field(default="", max_length=300)  # 在线目录（catalog.json）

    @field_validator("catalog_url")
    @classmethod
    def _https(cls, url: str) -> str:
        if url and not url.startswith("https://"):
            raise ValueError("catalog_url 要以 https:// 开头")
        return url


class ChatActionRules(_Strict):
    """聊天里能发什么（docs/CHAT.md「微信式动作」）：表情包、颜文字、引用、拆条、拍一拍、撤回。

    这里只管「能发什么」和简单的默认值；什么时候发、发不发、节奏和延迟由成员系统接管
    （``chat_actions.ActionPolicy``，没接管时按下面的默认值）。
    """

    enabled: bool = True
    # 成员能用的表情包：套装 → 名字（与 web/js/stickers.js 同名，有测试）
    stickers: dict[str, list[str]] = Field(default_factory=dict)
    kaomoji: list[str] = Field(default_factory=list, max_length=40)  # 颜文字示例
    emoji_images: EmojiImageRules = EmojiImageRules()  # 网页里的 emoji 显示成哪套图
    sticker_packs: StickerPackRules = StickerPackRules()  # 本机装的外部表情包
    max_segments: int = Field(default=3, ge=1, le=6)  # 一次回复最多拆成几条
    segment_gap_seconds: float = Field(default=1.2, ge=0, le=10)  # 拆条之间的固定间隔（默认值）
    recall_seconds: int = Field(default=120, ge=0, le=3600)  # 你发出后多久内可以撤回
    pat_reply: bool = True  # 被拍一拍后回一句（默认值）
    no_reply_in_group: bool = True  # 群里没被点名时允许不接话（默认值）
    drop_stale: bool = True  # 你补发新消息或撤回时，上一轮还没发出的气泡和还没开口的人作废
    # 今天上网分享过的成员发朋友圈时，有几成发成「一句话 + 转发卡片」（默认值）
    moment_forward: float = Field(default=0.5, ge=0, le=1)
    # 这些网站上的链接显示成视频卡片（只看网址，不联网）
    video_hosts: list[str] = Field(
        default_factory=lambda: ["youtube.com", "youtu.be", "vimeo.com", "bilibili.com"],
        max_length=20,
    )


class ChatRules(_Strict):
    """聊天：生活群、工作群、私聊。成员平时回应用户，按心情偶尔主动找（engine.reach）。"""

    # 每位成员一次回应最多想多久（含重试），超时就跳过这次，不卡住整轮
    think_timeout_seconds: float = Field(default=60.0, gt=0)
    life_title: str = "大家庭"  # 生活群的名字
    work_title: str = "工作群"
    budget: ChatBudgetRules = ChatBudgetRules()
    memory: MemoryRules = MemoryRules()
    life: LifeRules = LifeRules()
    browse: BrowseRules = BrowseRules()
    memes: MemeRules = MemeRules()
    moments: MomentsRules = MomentsRules()
    engine: EngineRules = EngineRules()
    watch: WatchRules = WatchRules()
    wander: WanderRules = WanderRules()

    actions: ChatActionRules = ChatActionRules()
    tier: Tier = "budget"  # 每位成员用这个档位的模型发言（没有就用该成员最便宜的）
    max_repliers: int = Field(default=2, ge=1, le=6)  # 没有 @ 人时，一条消息最多几位回应
    max_mentioned: int = Field(default=8, ge=1, le=12)  # @全员 / 所有人 / @多人 时最多几位回应
    # 你刚进群（在群里说过的话不到 newcomer_messages 条）时说「大家好」：最多 newcomer_max 位搭话
    # （10-31 用户反向找问题：新人进群 8 个人轮流自我介绍像机器人）
    newcomer_messages: int = Field(default=5, ge=0, le=100)
    newcomer_max: int = Field(default=3, ge=1, le=12)
    # 同一轮群聊里，后说的人和先说的人开头一样、条数一样（3 条以上）：后说的只发前 2 条
    same_shape_trim: bool = True
    history_messages: int = Field(default=30, ge=4, le=200)  # 发给模型的最近聊天条数
    max_tokens: int = Field(default=500, ge=64)
    max_chars: int = Field(default=800, ge=50)  # 一条消息最多保留的字数
    estimate_usd: float = Field(default=0.002, ge=0)  # 每条回应的预估花费（预算检查用）


class PlainTalkRule(_Strict):
    """说话底线规则表的一条（程序检查，不调用模型）。

    kind：phrase = 全文出现任一 patterns；ending = 最后一句匹配任一 patterns（金句收尾）；
    parallel = 连续 min_run 个以上分句开头相同（排比）：
    开头两个字相同，或开头一个字相同且在 heads 里；
    mug = 马克杯警戒线：某一句（6～max_chars 字）命中 patterns 里的空泛大词至少 min_hits 个、
    又不含 concrete 里任何一个具体所指（数字、这个、今天、书名号……），即脱离上下文也成立的金句。
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    problem: str = Field(min_length=1, max_length=80)  # 改写时告诉成员的问题说明
    kind: Literal["phrase", "ending", "parallel", "mug"]
    patterns: list[str] = Field(default_factory=list, max_length=60)  # 正则
    min_run: int = Field(default=3, ge=2, le=10)
    heads: list[str] = Field(default_factory=list, max_length=30)
    min_hits: int = Field(default=2, ge=1, le=10)  # mug：一句里至少几个空泛大词
    max_chars: int = Field(default=40, ge=6, le=200)  # mug：只查不超过这么多字的句子
    concrete: list[str] = Field(default_factory=list, max_length=60)  # mug：具体所指（正则）

    @field_validator("patterns", "concrete")
    @classmethod
    def _compile(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            try:
                re.compile(p)
            except re.error as exc:
                raise ValueError(f"正则写错了：{p!r}（{exc}）") from None
        return patterns

    @field_validator("heads")
    @classmethod
    def _single(cls, heads: list[str]) -> list[str]:
        bad = [h for h in heads if len(h) != 1]
        if bad:
            raise ValueError(f"heads 每项只能是一个字：{bad}")
        return heads

    @model_validator(mode="after")
    def _needs_patterns(self) -> PlainTalkRule:
        if self.kind != "parallel" and not self.patterns:
            raise ValueError(f"{self.kind} 规则至少要有一条 patterns")
        return self


class PlainTalkCheck(_Strict):
    """说话前的程序检查（成员自我监控的雏形）：只查聊天、生活、朋友圈的发言；查出问题才改写一次。"""

    enabled: bool = True
    min_chars: int = Field(default=12, ge=0)  # 短于这么多字的消息不查
    monthly_usd: float = Field(
        default=0.05, ge=0
    )  # 改写每月最多花多少（UTC 自然月；也计入聊天预算）
    estimate_usd: float = Field(default=0.0005, ge=0)  # 每次改写的预估花费（预算检查用）
    max_tokens: int = Field(default=600, ge=64)
    rules: list[PlainTalkRule] = Field(default_factory=list, max_length=30)

    @field_validator("rules")
    @classmethod
    def _unique(cls, rules: list[PlainTalkRule]) -> list[PlainTalkRule]:
        names = [r.name for r in rules]
        if len(names) != len(set(names)):
            raise ValueError(f"规则名不能重复：{names}")
        return rules


class PlainTalkRules(_Strict):
    """说话底线（去八股化）：roles 里的提示词每次调用都接上 plain_talk 提示词。

    同一步骤所有成员相同。
    """

    enabled: bool = True
    roles: list[str] = Field(default_factory=list)
    check: PlainTalkCheck = PlainTalkCheck()


class EvolutionSchedule(_Strict):
    """革新例会自动开会的时间（本机时区）：每周 weekday（ISO：1 = 周一）的 hour 点之后开一次。"""

    weekday: int = Field(default=1, ge=1, le=7)
    hour: int = Field(default=9, ge=0, le=23)


class EvolutionScoutRules(_Strict):
    """圆桌自己找资料写卡（docs/evolution/README.md「自己找资料」）：代码按搜索词联网搜索，只留白名单
    网站的结果；读网页正文后，一位成员写成一页资料卡，另一位成员复核（不自审），通过的才进收件箱。
    网页内容一律当资料（标签内的指令无效）；花费计入革新例会的每月上限。"""

    enabled: bool = False
    queries: list[str] = Field(default_factory=list, max_length=12)  # 搜索词（每次都搜）
    domains: list[str] = Field(default_factory=list, max_length=30)  # 白名单网站（含子域名）
    results_per_query: int = Field(default=5, ge=1, le=10)
    cards: int = Field(default=3, ge=1, le=10)  # 每次最多写几张（还受 max_cards 限制）
    page_chars: int = Field(default=6000, ge=500, le=30000)  # 每个网页最多读多少字
    max_tokens: int = Field(default=1200, ge=200)  # 写卡 / 复核的输出上限
    search_estimate_usd: float = Field(default=0.01, ge=0)  # 每次搜索 / 读网页的预估花费
    card_estimate_usd: float = Field(default=0.01, ge=0)  # 每张卡（写 + 复核）的预估花费
    seen_keep: int = Field(default=300, ge=10, le=5000)  # 记住多少个写过的网址（不重复写）

    @field_validator("domains")
    @classmethod
    def _bare_domains(cls, domains: list[str]) -> list[str]:
        out = []
        for d in domains:
            d = d.strip().lower()
            if not d or "/" in d or ":" in d or " " in d or "." not in d:
                raise ValueError(f"只写域名，例如 github.com：{d!r}")
            out.append(d)
        return out


class EvolutionImplementRules(_Strict):
    """「做这条」之后交给开发侧（docs/evolution/README.md）：在数据目录的专用工作副本里叫起开发侧
    命令行实现、开 PR；从不自动合并，用户在网页上点「合并」才合并。默认关闭。"""

    enabled: bool = False
    # 开发侧命令（任务说明从标准输入给）；第一个词找不到时也查用户安装位置
    agent: list[str] = Field(
        default_factory=lambda: [
            "claude",
            "-p",
            "--permission-mode",
            "acceptEdits",
            "--allowedTools",
            "Bash,Read,Edit,Write,Glob,Grep",
        ]
    )
    gh: list[str] = Field(default_factory=lambda: ["gh"])  # 合并 PR 用
    base_branch: str = Field(default="main", min_length=1)  # PR 合到哪个分支
    merge_method: Literal["merge", "squash", "rebase"] = "merge"
    brief: str = "docs/evolution/开发任务模板.md"  # 任务说明模板（相对项目根目录）
    workspace_dir: str = "工作区"  # 数据目录 evolution/ 下的专用工作副本
    timeout_minutes: int = Field(default=120, ge=5, le=600)

    @field_validator("workspace_dir")
    @classmethod
    def _plain(cls, name: str) -> str:
        if not name.strip() or any(c in name for c in "/\\:") or name.strip(".") == "":
            raise ValueError(f"只能是一个文件夹名（不含路径）：{name!r}")
        return name


class EvolutionPluginRules(_Strict):
    """革新例会·插件清单（docs/evolution/README.md「插件清单」）：例会开完后，在所选档位里按预计价格
    挑最便宜的一位成员，根据本场资料卡和点子榜写给用户看的插件清单（做什么、有什么用、优缺点、
    为什么做、用在哪个场景）。只是给用户看的资料，不改代码；花费计入革新例会的每月上限。"""

    enabled: bool = True
    count: int = Field(default=20, ge=5, le=40)  # 每场至少写几个
    scenes: list[str] = Field(
        default_factory=lambda: ["办公室工作", "公寓生活", "群聊", "朋友圈", "头脑风暴", "通用"],
        min_length=1,
        max_length=12,
    )  # 场景标签（最后一个兜底：成员写了不在清单里的场景时归到它）
    max_tokens: int = Field(default=8000, ge=1000)  # 每次调用的输出上限
    rounds: int = Field(default=2, ge=1, le=3)  # 不够 count 个时最多再补写几次（每次内容不同）
    estimate_usd: float = Field(default=0.04, ge=0)  # 每次调用的预估花费
    dir: str = "插件清单"  # 数据目录 evolution/ 下：每场一份 Markdown
    field_chars: int = Field(default=200, ge=40, le=1000)  # 每个字段最多多少字（多的截掉）

    @field_validator("dir")
    @classmethod
    def _plain(cls, name: str) -> str:
        if not name.strip() or any(c in name for c in "/\\:") or name.strip(".") == "":
            raise ValueError(f"只能是一个文件夹名（不含路径）：{name!r}")
        return name

    @field_validator("scenes")
    @classmethod
    def _scenes(cls, scenes: list[str]) -> list[str]:
        out = [s.strip() for s in scenes]
        if any(not s for s in out) or len(set(out)) != len(out):
            raise ValueError("场景不能为空，也不能重复")
        return out


class EvolutionRules(_Strict):
    """革新例会（docs/evolution/README.md）：成员读同一叠资料卡，复用头脑风暴提建议、匿名互评、投票，
    导出采纳单。默认关闭；花费有每场和每月硬上限（也计入总预算）。"""

    enabled: bool = False
    plan: str = Field(default="budget", min_length=1)  # 成员档位（routing.yaml 的 plans）：全员上桌
    anonymous: bool = True  # 匿名：塔罗牌代号
    topic: str = Field(default="", max_length=2000)  # 例会的话题（作为题目，资料卡作为附件）
    inbox_dir: str = "收件箱"  # 资料卡放这里（数据目录下的 evolution/ 里）
    adopted_dir: str = "采纳单"  # 导出的采纳单放这里
    max_cards: int = Field(default=5, ge=1, le=10)  # 每场最多几张资料卡（多的不读）
    card_chars: int = Field(default=3000, ge=200, le=20000)  # 每张卡最多多少字（多的截掉）
    meeting_usd: float = Field(default=0.40, gt=0)  # 每场预计花费上限：超出就不开
    monthly_usd: float = Field(
        default=1.5, gt=0
    )  # 每月（UTC 自然月）上限：已花 + 本场预计超出就不开
    # 两种例会：小版本（主题不限）列前 minor_top 名；大版本（围绕一个主题）列前 major_top 名。
    # 开哪种由代码按时间自动判断：每个自然月的第一场开大版本（两次大版本至少隔 major_gap_days 天），
    # 其余开小版本；你可以手动改。大版本的主题：你定了就用你的，否则在 major_themes 里轮换。
    minor_top: int = Field(default=3, ge=1, le=30)
    major_top: int = Field(default=10, ge=1, le=30)
    major_gap_days: int = Field(default=21, ge=7, le=90)
    major_themes: list[str] = Field(default_factory=list, max_length=20)  # 大版本主题的现成选项
    theme_chars: int = Field(default=200, ge=10, le=1000)  # 大版本主题最多多少字
    auto: bool = True  # 开启后每周按 schedule 自动开一次（服务开着才会开）
    schedule: EvolutionSchedule = EvolutionSchedule()
    tick_minutes: int = Field(default=30, ge=5, le=360)  # 服务开着时多久检查一次
    used_dir: str = "已用"  # 开过会的资料卡移到这里（按周分文件夹），下周不会再读
    scout: EvolutionScoutRules = EvolutionScoutRules()
    implement: EvolutionImplementRules = EvolutionImplementRules()
    plugins: EvolutionPluginRules = EvolutionPluginRules()

    @field_validator("inbox_dir", "adopted_dir", "used_dir")
    @classmethod
    def _plain_dir(cls, name: str) -> str:
        if not name.strip() or any(c in name for c in "/\\:") or name.strip(".") == "":
            raise ValueError(f"只能是一个文件夹名（不含路径）：{name!r}")
        return name

    @model_validator(mode="after")
    def _limits(self) -> EvolutionRules:
        if self.meeting_usd > self.monthly_usd:
            raise ValueError("meeting_usd 不能大于 monthly_usd")
        if self.enabled and not self.topic.strip():
            raise ValueError("开启革新例会时必须填写 topic")
        return self


class MailAIRules(_Strict):
    """终端邮件栏的 AI 总结 / 翻译（docs/MAIL.md）：只在用户点了按钮、
    并同意把这封邮件发给模型后才调用；所选档位里按预计价格挑最便宜的一位；
    花费计入总预算和这里的每月上限，每天次数也有上限。"""

    plan: str = "budget"
    monthly_usd: float = Field(default=0.30, ge=0)
    daily_calls: int = Field(default=20, ge=0, le=500)
    max_chars: int = Field(default=6000, ge=500, le=40000)  # 每封邮件最多发多少字
    max_tokens: int = Field(default=900, ge=100, le=8000)
    estimate_usd: float = Field(default=0.005, ge=0)  # 每次调用预估
    digest_mails: int = Field(default=10, ge=1, le=30)  # 「总结重要邮件」最多带几封
    language: str = Field(default="简体中文", min_length=1, max_length=20)  # 翻译成什么语言


class MailConfig(_Strict):
    ai: MailAIRules = MailAIRules()


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


class SuperWorker(_Strict):
    """超级电脑的一位「员工」：一个在电脑上用官方命令行跑的编程助手（docs/SUPERCOMPUTER.md）。

    谁擅长什么、每天能用几次都写在配置里；派活的程序只看这些数字，不看是谁。"""

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,23}$")
    label: str = Field(min_length=1, max_length=24)  # 界面上的称呼
    # 怎么启动：command = 按 argv 直接运行；dsh = 交给圆桌的 dsh 工作台
    # （用它的运行时、叠加配置和 key）
    via: Literal["command", "dsh"] = "command"
    # 命令行（via=command）；任务正文从标准输入交给它，不放进参数
    argv: list[str] = Field(default_factory=list)
    skills: dict[str, float] = Field(default_factory=dict)  # 活的种类 → 擅长程度 0～1
    daily_limit: int = Field(default=20, ge=0, le=1000)  # 每天最多派几件（界面上可改）
    max_parallel: int = Field(default=1, ge=1, le=8)  # 同时最多干几件
    timeout_s: float = Field(default=1800, gt=0, le=7200)
    cooldown_minutes: float = Field(default=60, ge=0, le=1440)  # 撞到订阅限额后歇多久
    # 输出里出现这些（不分大小写）就当作撞到订阅限额
    limit_patterns: list[str] = Field(
        default_factory=lambda: ["usage limit", "rate limit", "limit reached", "quota"]
    )

    @model_validator(mode="after")
    def _argv(self) -> SuperWorker:
        if self.via == "command" and not self.argv:
            raise ValueError(f"{self.id}：via=command 时要写 argv")
        for kind, v in self.skills.items():
            if not 0 <= v <= 1:
                raise ValueError(f"{self.id}.skills.{kind} 要在 0～1 之间")
        return self


class SuperWeights(_Strict):
    """派活打分的权重：分数 = 擅长 × skill + 剩余额度比例 × quota − 手上的活 × busy。"""

    skill: float = Field(default=0.6, ge=0)
    quota: float = Field(default=0.3, ge=0)
    busy: float = Field(default=0.2, ge=0)


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


class BenchRules(_Strict):
    """工作台生成器（docs/KIT.md）：一句话 → 拆需求 → 按零件库拼窗口 → 质检 + 截图评审 → 打回重做。

    活都交给超级电脑的几位助手（按分数挑人，花的是订阅次数）；评审永远不分给窗口的作者本人。
    做满 ``max_rounds`` 轮还不过线就停下来，交给用户决定。"""

    enabled: bool = True
    max_windows: int = Field(default=8, ge=1, le=16)  # 一个工作台最多几个窗口
    max_rounds: int = Field(default=3, ge=1, le=6)  # 质检 + 评审 + 返工最多几轮
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
    project: BenchProject = Field(default_factory=BenchProject)
    team: BenchTeam = Field(default_factory=BenchTeam)
    materials: BenchMaterials = Field(default_factory=BenchMaterials)
    skills: BenchSkills = Field(default_factory=BenchSkills)
    desk: BenchDesk = Field(default_factory=BenchDesk)
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
        cur = self.display_currency
        if cur != "USD" and cur not in {k.upper() for k in self.usd_rates}:
            raise ValueError(f"display_currency {cur} 没有汇率：在 usd_rates 里加上它")
        return self

    def display_rate(self) -> float:
        """1 美元合多少显示币种。"""
        rates = {k.upper(): v for k, v in self.usd_rates.items()}
        return 1.0 if self.display_currency == "USD" else rates[self.display_currency]


class CalendarRegion(_Strict):
    """一个国家 / 地区的法定假日（开源库 holidays 离线计算）。"""

    country: str = Field(pattern=r"^[A-Z]{2}$")  # ISO 国家代码，如 GB、CN、US
    subdiv: str | None = None  # 地区，如 GB 的 ENG（英格兰）
    label: str = Field(min_length=1, max_length=12)  # 界面和事件里的称呼，如「英国」
    language: str | None = None  # 节日名的语言（holidays 支持的才行，如 zh_CN）


_DATE_RULE = re.compile(
    r"^(\d{2}-\d{2}|easter[+-]\d{1,3}|[1-5]-(mon|tue|wed|thu|fri|sat|sun)-\d{2}"
    r"|last-(mon|tue|wed|thu|fri|sat|sun)-\d{2})$"
)


class CalendarObservance(_Strict):
    """不放假的节日 / 纪念日。rule：MM-DD；easter+N / easter-N（复活节前后 N 天）；
    N-周几-MM（某月第 N 个周几，如 2-sun-05）；last-周几-MM（某月最后一个周几）。"""

    name: str = Field(min_length=1, max_length=20)
    rule: str
    label: str = Field(default="国际", max_length=12)  # 哪里的节日

    @field_validator("rule")
    @classmethod
    def _rule(cls, rule: str) -> str:
        if not _DATE_RULE.match(rule):
            raise ValueError(
                f"日期规则写法不对：{rule}（MM-DD / easter±N / 2-sun-05 / last-sun-10）"
            )
        return rule


class LunarFestival(_Strict):
    """农历节日：lunar 写 MM-DD，或 MM-last（这个月最后一天，如除夕 12-last）。"""

    name: str = Field(min_length=1, max_length=12)
    lunar: str = Field(pattern=r"^\d{2}-(\d{2}|last)$")


class CalendarRules(_Strict):
    """日历底座（docs/CALENDAR.md）：只算「今天是什么日子」，发事件给成员系统；不调模型、不联网。

    怎么过节由生活引擎 / 成员系统决定；工作模式不挂载。"""

    enabled: bool = True
    # 成员的休息日按哪里的法定假日算（成员住曼彻斯特：英格兰）
    rest: CalendarRegion | None = CalendarRegion(country="GB", subdiv="ENG", label="英国")
    cultures: list[CalendarRegion] = Field(default_factory=list)  # 也过、但不放假的国家
    lunar_festivals: list[LunarFestival] = Field(default_factory=list)
    solar_terms: bool = True  # 二十四节气
    observances: list[CalendarObservance] = Field(default_factory=list)
    names: dict[str, str] = Field(default_factory=dict)  # 外文节日名 → 中文
    upcoming_days: int = Field(default=3, ge=0, le=30)  # 提前几天发「快到了」
    board_days: int = Field(default=7, ge=1, le=60)  # 公告栏显示未来几天
    max_user_dates: int = Field(default=100, ge=0, le=1000)  # 你能加的纪念日上限


class BackupRules(_Strict):
    """整库备份（docs/BACKUP.md）：每天一份 + 存档点，坏了一键恢复；不花钱。"""

    enabled: bool = True  # 每天自动备份（关掉后仍能手动存档点、恢复）
    keep_daily: int = Field(default=14, ge=1, le=365)  # 每天的备份留几份
    keep_checkpoints: int = Field(default=10, ge=1, le=100)  # 存档点留几份（和每天的分开数）
    check_minutes: int = Field(default=60, ge=5, le=1440)  # 开着时每隔多久看一眼「今天备份了没」


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


class StickerCard(_Strict):
    """一位成员的形象卡 + 专属表情清单（只用于聊天、朋友圈；工作模式不读）。"""

    look: str = Field(min_length=1, max_length=400)  # 外观要点
    must: list[str] = Field(default_factory=list, max_length=8)  # 每张都必须画到的细节
    # 外观的英文标签（逗号分隔，给吃标签的本地绘图模型用，见 moment_photo_tags）；不写时用 look
    look_tags: str = Field(default="", max_length=400)
    # 形象参考图：素材目录（members.json 所在目录）里的相对路径，最多 3 张；
    # 不写时用 members.json 里的立绘 / 小人 / 头像
    refs: list[str] = Field(default_factory=list, max_length=3)
    stickers: list[StickerSpec] = Field(default_factory=list, max_length=24)
    # 朋友圈配图用：标志小物件（只露手、背影时也认得出是谁，如小鲸鱼发卡）
    cues: list[str] = Field(default_factory=list, max_length=4)
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
        return self

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
