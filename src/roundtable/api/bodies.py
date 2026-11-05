"""HTTP 请求体（pydantic，多余字段报错）。

从 app.py 拆出（代码精简第 2 批）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from roundtable.core.service import MAX_QUESTION_CHARS


class ViewReplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=300)


class AboutFactBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=300)
    domain: str | None = Field(default=None, max_length=40)


class AboutLockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    locked: bool = True


class AboutDomainBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(max_length=40)
    on: bool


class AboutStatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(max_length=20)
    on: bool


class AboutPlayerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    player: str = Field(max_length=20)


class AboutGameBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exe: str = Field(max_length=80)
    name: str = Field(max_length=30)


class AboutClassBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weekday: int = Field(ge=0, le=6)
    start: str = Field(max_length=5)
    end: str = Field(max_length=5)
    name: str = Field(max_length=30)


class AboutDdlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=30)
    due: str = Field(max_length=16)


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 旧字段（mode / preset）直接报错，不悄悄忽略

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    tier: str | None = None  # 档位名或 custom（自选）
    models: list[str] = Field(default_factory=list)
    coordinator: str | None = None
    anonymous: bool = False
    workflow: str = "discussion"  # discussion 讨论 / collab 协同
    seed: int | None = None
    attachments: list[str] = Field(default_factory=list)  # POST /api/uploads 返回的附件 id
    media: str | None = None  # 讨论模式的输出类型：image / speech / video / music
    media_tier: str | None = None  # 媒体模型的档位：budget / flagship
    # 作为风格参考的图片附件 id；省略 = 全部图片都是风格参考，[] = 都不是
    style_refs: list[str] | None = None
    carry_from: str | None = None  # 接力：上一场的会话 id（带上它的成果摘要）


class EstimateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    tier: str | None = None
    models: list[str] = Field(default_factory=list)
    coordinator: str | None = None
    workflow: str | None = None  # 为空时两种模式都给出
    anonymous: bool = False
    attachments: list[str] = Field(default_factory=list)
    seed: int | None = None
    media: str | None = None
    media_tier: str | None = None
    carry_from: str | None = None


class DecisionBody(BaseModel):
    """革新例会：你对一条建议的决定（do = 做这条，skip = 不做）。"""

    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern="^(do|skip)$")


class MeetBody(BaseModel):
    """革新例会：开哪种会。kind 不给 = 按时间自动判断；给了 = 你手动改成 minor / major。"""

    model_config = ConfigDict(extra="forbid")

    kind: str | None = Field(default=None, pattern="^(minor|major)$")
    theme: str | None = None


class ThemeBody(BaseModel):
    """革新例会：下一次大版本的主题（空 = 交给自动轮换）。"""

    model_config = ConfigDict(extra="forbid")

    theme: str | None = None


class DrawChannelsBody(BaseModel):
    """出图渠道的手动设置：disabled 关掉的模型 id；order 手动先后（不传 = 不改）。"""

    model_config = ConfigDict(extra="forbid")

    disabled: list[str] | None = None
    order: list[str] | None = None


class ChannelPriorityBody(BaseModel):
    """渠道优先顺序：auto 按配置 / custom 按 order；credits 为每月额度（null = 不设）。"""

    model_config = ConfigDict(extra="forbid")

    strategy: str
    order: list[str] | None = None
    credits: dict[str, float | None] | None = None


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    room: str = "main"
    media: list[dict[str, Any]] = Field(default_factory=list)  # POST /api/chat/media 返回的 id
    quote: int | None = None  # 引用的消息 id
    files: list[str] = Field(default_factory=list, max_length=5)  # POST /api/uploads 返回的附件 id


class PatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member: str
    room: str = "main"


class ForwardBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    room: str


class RecallBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    room: str = "main"


class HandoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class VoiceBody(BaseModel):
    text: str | None = Field(default=None, max_length=2000)  # 省略：念一句试听


class StickerPackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=64)  # 装的时候用：目录里的包 id
    owner: str | None = Field(default=None, max_length=40)  # 专用的成员；空串 = 通用；不传 = 不变
    label: str | None = Field(default=None, max_length=6)  # 聊天里的套装名


class StickerReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "approved", "rejected"]


class PersonaBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persona: dict[str, Any] | None = None  # None：恢复为配置文件里的


class MomentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    media: list[dict[str, Any]] = Field(default_factory=list)


class MomentCommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    reply_to: int | None = None


class ClipOpenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2000)


class ClipSearchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(max_length=200)


class ClipSaveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(max_length=2000)
    title: str = Field(default="", max_length=300)
    text: str = Field(max_length=200000)
    partial: bool = False  # 只存了选中的段落
    note: str = Field(default="", max_length=300)


class ProfilePatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: dict[str, dict[str, Any]]
    reason: str = Field(default="", max_length=200)


class ProfileLockBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=3, max_length=100)
    locked: bool = True


class ProfileImportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=500_000)  # 人物卡 Markdown
    mapping: dict[str, str] | None = None  # 卡名 → 成员 id（空串 = 跳过）
    dry_run: bool = False


class ProfileRevertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class GrowthUndoBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=200)  # 成长总账里那一条的 undo


class GrowthRevertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1)


class FeedsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feeds: list[dict[str, Any]] | None = None  # None：恢复为配置文件里的


class MemeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(min_length=1, max_length=20)
    aliases: list[str] = Field(default_factory=list, max_length=6)
    meaning: str = Field(default="", max_length=80)
    cue: str = Field(default="", max_length=40)
    reply: str = Field(default="", max_length=40)
    example: str = Field(default="", max_length=80)
    classic: bool = True


class MemoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    member: str | None = None  # 新建时必填


class GameModeBody(BaseModel):
    """游戏模式的手动开关（docs/GAMEMODE.md）。"""

    model_config = ConfigDict(extra="forbid")
    manual: bool


class HomeTzBody(BaseModel):
    """主时区：IANA 名字（如 Europe/London）或 local（跟随电脑）。"""

    model_config = ConfigDict(extra="forbid")

    zone: str = Field(max_length=64)


class CalendarDateBody(BaseModel):
    """你自己加的纪念日：date 写 YYYY-MM-DD（会算第几周年）或 MM-DD；lunar 为农历。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    date: str
    lunar: bool = False


class MailSettingsBody(BaseModel):
    """邮件栏设置：只改给了的字段。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    rules: dict[str, Any] | None = None
    days: int | None = None
    show_window: bool | None = None
    extra_hosts: list[str] | None = None
    ai_ok: bool | None = None  # 同意 / 不再同意把点了的邮件发给 AI 总结、翻译


class MailRefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual: bool = True


class NowPlayingControlBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player: str = Field(default="qqmusic", max_length=40)
    action: str = Field(pattern="^(play|pause|toggle|next|prev|seek)$")
    position: float | None = Field(default=None, ge=0, le=86400)


class WatchStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=100)  # pc / pc:<窗口> / android:<序列号>
    watchers: list[str] = Field(min_length=1, max_length=6)
    consent: bool = False  # 用户点了「同意把截图发给识图模型」


class TripSwitchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto: bool | None = None  # B 站一键总开关
    photo: bool | None = None  # 打卡配图（默认关）


class BiliWatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=10, max_length=300)


class WanderStartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    member: str = Field(min_length=1, max_length=60)
    site: str | None = Field(default=None, max_length=20)  # 空 = 随她挑


class WatchQuietBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quiet: bool


class BenchProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=200)
    src: str = Field(default="", max_length=40)


class BenchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request: str = Field(min_length=1, max_length=2000)
    materials: list[str] = Field(default_factory=list, max_length=64)  # 先收下的素材编号
    from_product: bool = False  # 从成品反推：拖进来的是成品，要能做出类似成品的台
    form: dict[str, str] = Field(default_factory=dict, max_length=20)  # 按表填的需求（可选）


class BenchTreeKeyBody(BaseModel):
    """分工树里暂停 / 继续哪一块：「大阶段/小项目」，或 * 整个项目（继续时不写 = 全部继续）。"""

    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, max_length=120)


class BenchRerunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread: str = Field(min_length=1, max_length=40)
    reason: str = Field(default="", max_length=1000)
    worker: str | None = Field(default=None, max_length=40)


class BenchEditBody(BaseModel):
    """改一样东西（第 2 块）：key 见 core/benchkit/edits.py；value 按种类是文字、清单或字典。"""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    value: Any = None
    why: str = Field(default="", max_length=500)


class BenchRevertBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=80)
    n: int = Field(ge=1)


class BenchInstructBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window: str = Field(min_length=1, max_length=60)
    text: str = Field(default="", max_length=1000)
    worker: str | None = Field(default=None, max_length=40)


class BenchConflictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: Literal["now", "later"]  # 停下来马上重做 / 等这件活做完再说


class BenchFoundBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: str = Field(min_length=1, max_length=20)  # 找到的素材编号，如 f1-2


class BenchAttachBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=64)


class BenchAnswerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: dict[str, str] = Field(default_factory=dict)


class BenchReqItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", max_length=10)
    text: str = Field(max_length=300)


class BenchDesignBody(BaseModel):
    """选型台上点的：每项一个选项 id（程序会再按目录核对），参考作品、窗口宽窄、去掉的窗口。"""

    model_config = ConfigDict(extra="forbid")

    layout: str = Field(default="", max_length=20)
    theme: str = Field(default="", max_length=20)
    material: str = Field(default="", max_length=20)
    density: str = Field(default="", max_length=20)
    corner: str = Field(default="", max_length=20)
    type: str = Field(default="", max_length=20)
    width: str = Field(default="", max_length=20)
    topbar: str = Field(default="", max_length=20)
    herosize: str = Field(default="", max_length=20)
    bg: str = Field(default="", max_length=20)
    shadow: str = Field(default="", max_length=20)
    size: str = Field(default="", max_length=20)
    tone: str = Field(default="", max_length=20)
    button: str = Field(default="", max_length=20)
    cardhead: str = Field(default="", max_length=20)
    motion: str = Field(default="", max_length=20)
    accent: str | None = Field(default=None, max_length=7)  # #rrggbb；空字符串 = 用配色里的主色
    note: str | None = Field(default=None, max_length=200)  # 自己写一条风格要求
    assets: dict[str, str] = Field(default_factory=dict, max_length=64)  # 素材编号 → 用处
    refs: list[str] | None = Field(default=None, max_length=12)
    spans: dict[str, int] = Field(default_factory=dict, max_length=12)
    drop: list[str] = Field(default_factory=list, max_length=12)


class BenchSignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirements: list[BenchReqItem] | None = Field(default=None, max_length=30)
    note: str = Field(default="", max_length=1000)
    design: BenchDesignBody | None = None


class BenchDecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["more", "edit", "accept"]  # 再跑一轮 / 改要求再跑 / 就这样
    requirements: list[BenchReqItem] | None = Field(default=None, max_length=30)


class BenchComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)
    window: str = Field(default="", max_length=60)


class BenchReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["pass", "change"]
    comments: list[BenchComment] = Field(default_factory=list, max_length=20)


class BenchPluginBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)  # 调研报告里列出的插件（仓库名）


class BenchAskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=8000)
    as_json: bool = False  # 要结构化的结果（成品按用户的输入现场生成内容）


class SuperTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)
    queue: bool = False  # 超级电脑在忙时排进队（不然报「在忙」）


class SuperScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    at: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    days: list[int] = Field(default_factory=list, max_length=7)  # 0=周一；空=每天
    once: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class SuperToggleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SuperLimitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(ge=0, le=1000)


class WatchSayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)


class AdbPairBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=100)
    port: int = Field(ge=1, le=65535)
    code: str = Field(min_length=6, max_length=6)


class AdbConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="", max_length=100)  # 空 = 自动找已配对的设备
    port: int = Field(default=0, ge=0, le=65535)


class WatchScrcpyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=100)


class MailMarkBody(BaseModel):
    """邮件 id 里有斜杠，放在请求体里。mark 为空 = 交回规则判断。"""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=1000)
    mark: str | None = Field(default=None, pattern="^(important|normal)$")


class MailCategoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=1000)
    category: str | None = Field(default=None, max_length=40)


class MailIdBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=1000)


class MailAIBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(default=None, max_length=1000)  # 汇总不需要
    again: bool = False  # 重新生成（再花一次钱）


class MailReminderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(accept|dismiss|undo)$")


class ExportSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: str = Field(default="docx", max_length=16)
    folder: str = Field(max_length=1024)
    size: str | None = Field(default=None, max_length=16)
    theme: str | None = Field(default=None, max_length=16)
    ratio: str | None = Field(default=None, max_length=8)
    question: bool | None = None
    process: bool | None = None


class LanPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(max_length=256)


class BackupPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(default="手动", max_length=40)


class BackupRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)  # 备份列表里的文件名


class LanPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: str  # 平板要打开的地址，必须是 GET /api/lan 给出的其中一个


class Respond(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: str
    note: str | None = None
