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
MediaUnit = Literal["image", "second", "minute", "char"]


class MediaPrice(_Strict):
    """媒体模型每个单位的价格（美元）：图片每张、视频每秒、转写每分钟、语音合成每字符。"""

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
    """媒体生成（图片、语音、视频）：由 seat: false 的媒体模型完成，按标签和档位选择。"""

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
    entry_chars: int = Field(default=300, ge=50)  # 每条记忆最多多少字
    log_chars: int = Field(default=6000, ge=500)  # 整理一天时最多读多少字的聊天记录
    catch_up_days: int = Field(default=7, ge=1, le=60)  # 最多补整理几天前的
    max_tokens: int = Field(default=600, ge=64)
    estimate_usd: float = Field(default=0.001, ge=0)  # 每次整理的预估花费（预算检查用）
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


class LifeRules(_Strict):
    """生活引擎（docs/CHAT.md G4）：成员在用户不在时按作息过日子、彼此聊天。"""

    enabled: bool = True
    daily_usd: float = Field(default=0.10, ge=0)  # 生活（含记忆整理）每天最多花多少（UTC 日）
    tick_minutes: int = Field(default=30, ge=5, le=360)  # 电脑开着时多久推进一次
    max_written_per_tick: int = Field(default=2, ge=0, le=10)  # 每次推进最多几件事写成聊天
    max_written_catch_up: int = Field(default=6, ge=0, le=30)  # 开机补算时最多写几件
    catch_up_hours: int = Field(default=24, ge=1, le=24 * 14)  # 补算最多往回看多少小时
    turns: int = Field(default=3, ge=1, le=6)  # 一段成员之间的对话最多几句
    pair_chance: float = Field(default=0.15, ge=0, le=1)  # 每小时有人私下聊天的概率
    estimate_usd: float = Field(default=0.002, ge=0)  # 每句的预估花费（预算检查用）
    max_tokens: int = Field(default=800, ge=64)
    guard: LifeGuardRules = LifeGuardRules()


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


class EvalRules(_Strict):
    """评测集：所有成员相同的参数；同一题两位回复的相似度达到 clash_similarity 算撞型。"""

    max_tokens: int = Field(default=600, ge=64)
    clash_similarity: float = Field(default=0.6, gt=0, le=1)


class CardRules(_Strict):
    """随身卡（M1 自我认知）：每次调模型都带的那一小段，放在上下文组装器的固定前缀里。"""

    budget_chars: int = Field(default=400, ge=50, le=4000)


class EngineRules(_Strict):
    """成员引擎（docs/MEMBER_ENGINE.md）：只用于聊天、朋友圈、生活；工作模式不读取。"""

    enabled: bool = True
    profile: ProfileRules = ProfileRules()
    card: CardRules = CardRules()
    context: ContextRules = ContextRules()
    scheduler: SchedulerRules = SchedulerRules()
    heartbeat: HeartbeatRules = HeartbeatRules()
    bus: BusRules = BusRules()
    eval: EvalRules = EvalRules()


class ChatActionRules(_Strict):
    """聊天里能发什么（docs/CHAT.md「微信式动作」）：表情包、颜文字、引用、拆条、拍一拍、撤回。

    这里只管「能发什么」和简单的默认值；什么时候发、发不发、节奏和延迟由成员系统接管
    （``chat_actions.ActionPolicy``，没接管时按下面的默认值）。
    """

    enabled: bool = True
    # 成员能用的表情包：套装 → 名字（与 web/js/stickers.js 同名，有测试）
    stickers: dict[str, list[str]] = Field(default_factory=dict)
    kaomoji: list[str] = Field(default_factory=list, max_length=40)  # 颜文字示例
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
    """聊天：生活群、工作群、私聊。成员不主动找用户，只回应用户。"""

    life_title: str = "大家庭"  # 生活群的名字
    work_title: str = "工作群"
    budget: ChatBudgetRules = ChatBudgetRules()
    memory: MemoryRules = MemoryRules()
    life: LifeRules = LifeRules()
    browse: BrowseRules = BrowseRules()
    moments: MomentsRules = MomentsRules()
    engine: EngineRules = EngineRules()
    actions: ChatActionRules = ChatActionRules()
    tier: Tier = "budget"  # 每位成员用这个档位的模型发言（没有就用该成员最便宜的）
    max_repliers: int = Field(default=2, ge=1, le=6)  # 没有 @ 人时，一条消息最多几位回应
    max_mentioned: int = Field(default=8, ge=1, le=12)  # @全员 / 所有人 / @多人 时最多几位回应
    history_messages: int = Field(default=30, ge=4, le=200)  # 发给模型的最近聊天条数
    max_tokens: int = Field(default=500, ge=64)
    max_chars: int = Field(default=800, ge=50)  # 一条消息最多保留的字数
    estimate_usd: float = Field(default=0.002, ge=0)  # 每条回应的预估花费（预算检查用）


class PlainTalkRule(_Strict):
    """说话底线规则表的一条（程序检查，不调用模型）。

    kind：phrase = 全文出现任一 patterns；ending = 最后一句匹配任一 patterns（金句收尾）；
    parallel = 连续 min_run 个以上分句开头相同（排比）：
    开头两个字相同，或开头一个字相同且在 heads 里。
    """

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=40)
    problem: str = Field(min_length=1, max_length=80)  # 改写时告诉成员的问题说明
    kind: Literal["phrase", "ending", "parallel"]
    patterns: list[str] = Field(default_factory=list, max_length=60)  # 正则
    min_run: int = Field(default=3, ge=2, le=10)
    heads: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("patterns")
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
                if step.media not in (None, "image", "speech", "video"):
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
