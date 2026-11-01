"""FastAPI 应用：HTTP 接口 + SSE 实时推送。只调用 core.service。

启动：uvicorn roundtable.api.app:app --reload   （默认只监听本机）
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from roundtable.core.service import (
    CHAT_MEDIA_MAX,
    LAN_COOKIE,
    MAX_QUESTION_CHARS,
    LanAccess,
    RoundtableService,
    ServiceError,
    data_dir,
    is_local,
    is_remote,
    qr_svg,
)

WEB_DIR = Path(__file__).resolve().parents[3] / "web"
# 可以在页面里直接显示的图片类型（SVG 不在其中：只作为文本预览或下载）
INLINE_IMAGES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
# 生成的音频 / 视频可以在页面里播放（支持 Range，便于拖动进度条）
INLINE_MEDIA = frozenset({"audio/mpeg", "audio/wav", "audio/ogg", "video/mp4", "video/webm"})


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


# 生成的工作台在「沙箱」里运行：浏览器把它当成一个陌生网站，碰不到圆桌的接口、存储和登录状态
# 能联网取的只有配置里那几项公开数据（地图底图、天气、汇率……），其余网址一律拦下
def bench_csp(origins: list[str]) -> str:
    extra = "".join(f" {o}" for o in origins)
    return (
        "sandbox allow-scripts allow-forms allow-popups allow-downloads; "
        "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; "
        f"style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:{extra}; "
        "media-src 'self' data: blob:; "
        f"connect-src 'self'{extra}; frame-ancestors 'self'"
    )


class SuperTaskBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=8000)


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


def _byte_range(header: str | None, size: int) -> tuple[int, int] | str | None:
    """解析 Range: bytes=a-b（只支持单个区间）。没有 Range 返回 None；无法满足返回 "invalid"。"""
    if not header:
        return None
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header.strip())
    if not match or not (match.group(1) or match.group(2)):
        return None
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last), size - 1) if last else size - 1
    else:  # 最后 N 个字节
        start, end = max(0, size - int(last)), size - 1
    if start >= size or start > end:
        return "invalid"
    return start, end


def sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event['type']}\ndata: {data}\n\n"


# 浏览器里可能还存着加 no-cache 之前缓存的旧脚本：
# 打开页面时如果没见过这个版本号，就让浏览器清掉本站缓存一次
WEB_CACHE_EPOCH = "2"
WEB_CACHE_COOKIE = "rt_web"


class WebFiles(StaticFiles):
    """前端静态文件：每次打开都向服务器确认是否有新版本（ETag 未变时只回 304），
    否则浏览器会按修改时间自行缓存旧脚本，改完界面刷新后看到的还是旧的。"""

    def file_response(
        self, full_path: Any, stat_result: Any, scope: Any, *args: Any, **kwargs: Any
    ) -> Response:
        resp = super().file_response(full_path, stat_result, scope, *args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        if "/kit/" in str(full_path).replace("\\", "/"):
            # 设计套件：沙箱里的工作台（来源是「陌生网站」）也要能用 import 载入
            resp.headers["Access-Control-Allow-Origin"] = "*"
        if (
            str(full_path).endswith(".html")
            and Request(scope).cookies.get(WEB_CACHE_COOKIE) != WEB_CACHE_EPOCH
        ):
            resp.headers["Clear-Site-Data"] = '"cache"'
            resp.set_cookie(WEB_CACHE_COOKIE, WEB_CACHE_EPOCH, max_age=365 * 86400, samesite="lax")
        return resp


# 局域网开放时，没登录的设备也能打开的地址：登录页、配对链接、添加到主屏幕用的清单和图标
LAN_PUBLIC = frozenset(
    {"/login.html", "/api/lan/login", "/lan/pair", "/manifest.webmanifest", "/favicon.ico"}
)
LAN_PUBLIC_PREFIX = ("/img/app/",)


def _client(scope: dict[str, Any]) -> str | None:
    client = scope.get("client")
    return client[0] if client else None


def _host_is_literal(scope: dict[str, Any]) -> bool:
    """Host 头是 IP、localhost 或 Tailscale 的 MagicDNS 名字（*.ts.net）。

    挡住 DNS 重绑定：别的网站把自己的域名解析到这台电脑。ts.net 的名字只有 Tailscale 能分配。"""
    host = ""
    for key, value in scope.get("headers", []):
        if key == b"host":
            host = value.decode("latin-1")
            break
    name = host.rsplit(":", 1)[0] if not host.startswith("[") else host[1 : host.find("]")]
    name = name.lower().rstrip(".")
    return name in ("localhost",) or name.endswith(".ts.net") or is_local(name) or is_remote(name)


class LanGate:
    """局域网访问守门（纯 ASGI 中间件，不缓冲 SSE）。

    没开放（默认）：别的设备的请求一律 403（就算有人手动用 --host 0.0.0.0 启动）。
    开放后：本机照常；别的设备要先登录，没登录时页面跳到登录页、接口回 401。
    """

    def __init__(self, app: Any, lan: LanAccess) -> None:
        self.app = app
        self.lan = lan

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client, path = _client(scope), scope.get("path", "")
        if not self.lan.enabled:
            if is_remote(client):
                await _deny(scope, receive, send, 403, "圆桌没有对局域网开放")
                return
        elif is_remote(client) and not self.lan.allows(client):
            await _deny(scope, receive, send, 403, "圆桌现在只对 Tailscale 里的设备开放")
            return
        elif not _host_is_literal(scope):
            await _deny(scope, receive, send, 403, "请用 IP 地址打开圆桌")
            return
        elif not is_local(client) and not (
            path in LAN_PUBLIC
            or path.startswith(LAN_PUBLIC_PREFIX)
            or self.lan.verify(Request(scope).cookies.get(LAN_COOKIE))
        ):
            if path.startswith("/api/") or scope["type"] != "http":
                await _deny(scope, receive, send, 401, "请先登录")
            else:
                await RedirectResponse("/login.html", status_code=303)(scope, receive, send)
            return
        await self.app(scope, receive, send)


async def _deny(scope: dict[str, Any], receive: Any, send: Any, status: int, text: str) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    await JSONResponse({"detail": text}, status_code=status)(scope, receive, send)


def _lan_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(LAN_COOKIE, token, max_age=30 * 86400, httponly=True, samesite="lax", path="/")


def create_app(service: RoundtableService | None = None, lan: LanAccess | None = None) -> FastAPI:
    """service 为空时在启动时根据 .env 与配置构建（测试中传入使用 Fake 模型的服务）。

    lan 为空时：正式启动按环境变量 ROUNDTABLE_LAN 决定是否对局域网开放；测试中默认不开放。
    """
    if lan is None:
        lan = LanAccess.from_env(data_dir()) if service is None else LanAccess(None)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if service is not None:
            app.state.service = service
            yield
            return
        from roundtable.core.runtime import Runtime

        app.state.service = RoundtableService(Runtime.build())
        app.state.service.reconcile()
        app.state.service.start_backup()
        app.state.service.start_life()
        app.state.service.start_evolution()
        app.state.service.start_dsh()
        try:
            yield
        finally:
            await app.state.service.aclose()

    app = FastAPI(title="Roundtable", lifespan=lifespan)
    app.state.lan = lan
    app.add_middleware(LanGate, lan=lan)

    def svc(request: Request) -> RoundtableService:
        return request.app.state.service

    @app.exception_handler(ServiceError)
    async def service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        return svc(request).status()

    @app.get("/api/budget")
    async def budget(request: Request) -> dict[str, Any]:
        return svc(request).budget()

    @app.get("/api/usage")
    async def usage(request: Request, range: str = "month") -> dict[str, Any]:  # noqa: A002
        return svc(request).usage(range)

    @app.get("/api/usage/balance")
    async def usage_balance(request: Request, refresh: int = 0) -> dict[str, Any]:
        return await svc(request).account_balance(refresh=bool(refresh))

    @app.get("/api/channels/priority")
    async def channel_priority(request: Request) -> dict[str, Any]:
        return svc(request).channel_priority()

    @app.put("/api/channels/priority")
    async def set_channel_priority(request: Request, body: ChannelPriorityBody) -> dict[str, Any]:
        return svc(request).set_channel_priority(
            strategy=body.strategy, order=body.order, credits=body.credits
        )

    @app.get("/api/members")
    async def members(request: Request) -> dict[str, Any]:
        return svc(request).members()

    @app.get("/api/members/files/{path:path}")
    async def member_file(request: Request, path: str) -> Response:
        data, mime = svc(request).member_file(path)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
        return Response(content=data, media_type=mime, headers=headers)

    @app.get("/api/standee")
    async def standee(request: Request) -> dict[str, Any]:
        return svc(request).standee()

    @app.get("/api/standee/files/{path:path}")
    async def standee_file(request: Request, path: str) -> Response:
        data, mime = svc(request).standee_file(path)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
        return Response(content=data, media_type=mime, headers=headers)

    @app.get("/api/nowplaying")
    async def nowplaying(request: Request, player: str = "qqmusic") -> dict[str, Any]:
        return await asyncio.to_thread(svc(request).nowplaying, player)

    @app.get("/api/nowplaying/cover")
    async def nowplaying_cover(request: Request, player: str = "qqmusic") -> Response:
        data, mime = await asyncio.to_thread(svc(request).nowplaying_cover, player)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=600"}
        return Response(content=data, media_type=mime, headers=headers)

    @app.post("/api/nowplaying/control")
    async def nowplaying_control(request: Request, body: NowPlayingControlBody) -> dict[str, Any]:
        return await asyncio.to_thread(
            svc(request).nowplaying_control, body.player, body.action, body.position
        )

    # ---- 投屏陪伴（docs/WATCH.md）：开始投屏、看预览、开 scrcpy 只能在电脑本机上；
    # 平板 / 手机上能看状态和聊天、能随时停止 ----

    def watch_local(request: Request) -> None:
        if is_remote(_client(request.scope)):
            raise ServiceError("投屏只能在电脑上开始和预览；平板上可以聊天和停止", 403)

    @app.get("/api/watch")
    async def watch_status(request: Request, devices: bool = False) -> dict[str, Any]:
        if devices:
            watch_local(request)
            return await asyncio.to_thread(svc(request).watch_status, True)
        return svc(request).watch_status()

    @app.post("/api/watch/start")
    async def watch_start(request: Request, body: WatchStartBody) -> dict[str, Any]:
        watch_local(request)
        return await svc(request).watch_start(body.source, body.watchers, body.consent)

    @app.post("/api/watch/stop")
    async def watch_stop(request: Request) -> dict[str, Any]:
        return svc(request).watch_stop()

    @app.post("/api/watch/quiet")
    async def watch_quiet(request: Request, body: WatchQuietBody) -> dict[str, Any]:
        return svc(request).watch_quiet(body.quiet)

    @app.get("/api/watch/frame")
    async def watch_frame(request: Request) -> Response:
        watch_local(request)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        return Response(
            content=svc(request).watch_frame(), media_type="image/jpeg", headers=headers
        )

    @app.get("/api/watch/messages")
    async def watch_messages(request: Request, after: int = 0) -> dict[str, Any]:
        return svc(request).watch_messages(after)

    @app.post("/api/watch/say", status_code=202)
    async def watch_say(request: Request, body: WatchSayBody) -> dict[str, Any]:
        return await svc(request).watch_say(body.text)

    # dsh 工作台：网页地址带一次性口令，只给电脑本机（dsh 只监听 127.0.0.1，平板也打不开）
    def dsh_out(request: Request, out: dict[str, Any]) -> dict[str, Any]:
        if out.get("url") and not is_local(_client(request.scope)):
            out = {**out, "url": None, "local_only": True}
        return out

    @app.get("/api/dsh")
    async def dsh_status(request: Request) -> dict[str, Any]:
        return dsh_out(request, svc(request).dsh_status())

    @app.post("/api/dsh/start")
    async def dsh_start(request: Request) -> dict[str, Any]:
        return dsh_out(request, await svc(request).dsh_start())

    @app.post("/api/dsh/stop")
    async def dsh_stop(request: Request) -> dict[str, Any]:
        return dsh_out(request, await svc(request).dsh_stop())

    @app.post("/api/dsh/check")
    async def dsh_check(request: Request) -> dict[str, Any]:
        return dsh_out(request, await svc(request).dsh_check())

    # 超级电脑：几位编程助手平级、按分数派活（docs/SUPERCOMPUTER.md）
    @app.get("/api/bench")
    async def bench_status(request: Request) -> dict[str, Any]:
        return svc(request).bench_status()

    @app.post("/api/bench")
    async def bench_submit(request: Request, body: BenchBody) -> dict[str, Any]:
        return svc(request).bench_submit(
            body.request, body.materials, from_product=body.from_product
        )

    @app.post("/api/bench/materials", status_code=201)
    async def bench_material(request: Request, name: str, of: str = "") -> dict[str, Any]:
        """拖进来的一份素材：请求体是原始字节，?name= 文件名，?of= 视频编号（浏览器截的帧）。"""
        service = svc(request)
        limit = service.bench_material_limit
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise ServiceError("文件超过大小上限", 413)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise ServiceError("文件超过大小上限", 413)
            chunks.append(chunk)
        return service.bench_material(name, b"".join(chunks), of)

    @app.post("/api/bench/cancel")
    async def bench_cancel(request: Request) -> dict[str, Any]:
        return await svc(request).bench_cancel()

    @app.get("/api/bench/net-check")
    async def bench_net_check(request: Request) -> dict[str, Any]:
        return await svc(request).bench_net_check()

    @app.post("/api/bench/profile")
    async def bench_profile_add(request: Request, body: BenchProfileBody) -> dict[str, Any]:
        return svc(request).bench_profile_add(body.text, body.src)

    @app.delete("/api/bench/profile/{pid}")
    async def bench_profile_remove(request: Request, pid: str) -> dict[str, Any]:
        return svc(request).bench_profile_remove(pid)

    @app.get("/api/bench/desk")
    async def bench_desk(request: Request) -> dict[str, Any]:
        return svc(request).bench_desk()

    @app.post("/api/bench/desk/refresh")
    async def bench_desk_refresh(request: Request) -> dict[str, Any]:
        return svc(request).bench_desk(refresh=True)

    @app.post("/api/bench/desk/suggest/{sid}")
    async def bench_desk_suggest(request: Request, sid: str, accept: bool = True) -> dict[str, Any]:
        return svc(request).bench_desk_suggest(sid, accept)

    @app.get("/api/bench/{bench_id}")
    async def bench_get(request: Request, bench_id: str) -> dict[str, Any]:
        return svc(request).bench_get(bench_id)

    @app.post("/api/bench/{bench_id}/answer")
    async def bench_answer(
        request: Request, bench_id: str, body: BenchAnswerBody
    ) -> dict[str, Any]:
        return await svc(request).bench_project(bench_id, "answer", answers=body.answers)

    @app.post("/api/bench/{bench_id}/decide")
    async def bench_decide(
        request: Request, bench_id: str, body: BenchDecideBody
    ) -> dict[str, Any]:
        reqs = [r.model_dump() for r in body.requirements or []]
        return await svc(request).bench_project(
            bench_id, "decide", choice=body.action, requirements=reqs
        )

    @app.post("/api/bench/{bench_id}/sign")
    async def bench_sign(request: Request, bench_id: str, body: BenchSignBody) -> dict[str, Any]:
        reqs = (
            [r.model_dump() for r in body.requirements] if body.requirements is not None else None
        )
        design = body.design.model_dump(exclude_none=True) if body.design is not None else None
        return await svc(request).bench_project(
            bench_id, "sign", requirements=reqs, note=body.note, design=design
        )

    @app.post("/api/bench/{bench_id}/review")
    async def bench_review(
        request: Request, bench_id: str, body: BenchReviewBody
    ) -> dict[str, Any]:
        return await svc(request).bench_project(
            bench_id,
            "review",
            decision=body.decision,
            comments=[c.model_dump() for c in body.comments],
        )

    @app.post("/api/bench/{bench_id}/materials")
    async def bench_attach(
        request: Request, bench_id: str, body: BenchAttachBody
    ) -> dict[str, Any]:
        return await svc(request).bench_project(bench_id, "attach", ids=body.ids)

    @app.post("/api/bench/{bench_id}/look")
    async def bench_look(request: Request, bench_id: str) -> dict[str, Any]:
        return await svc(request).bench_project(bench_id, "look")

    @app.post("/api/bench/{bench_id}/evolve")
    async def bench_evolve(request: Request, bench_id: str) -> dict[str, Any]:
        return await svc(request).bench_project(bench_id, "evolve")

    @app.post("/api/bench/{bench_id}/plugin")
    async def bench_plugin(
        request: Request, bench_id: str, body: BenchPluginBody
    ) -> dict[str, Any]:
        return await svc(request).bench_project(bench_id, "plugin", name=body.name)

    @app.post("/api/bench/{bench_id}/ask")
    async def bench_ask(request: Request, bench_id: str, body: BenchAskBody) -> dict[str, Any]:
        return await svc(request).bench_ask(bench_id, body.question, body.as_json)

    @app.get("/bench/{bench_id}/{path:path}")
    async def bench_file(request: Request, bench_id: str, path: str) -> Response:
        full = svc(request).bench_file(bench_id, path)
        mime = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
        if full.suffix in (".js", ".mjs"):
            mime = "text/javascript"
        csp = bench_csp(svc(request).bench_net_origins())
        headers = {"Cache-Control": "no-cache", "Content-Security-Policy": csp}
        headers["Access-Control-Allow-Origin"] = "*"  # 沙箱页面载入自己的模块和数据
        return Response(content=full.read_bytes(), media_type=mime, headers=headers)

    @app.get("/api/super")
    async def super_status(request: Request) -> dict[str, Any]:
        return svc(request).super_status()

    @app.post("/api/super/tasks")
    async def super_submit(request: Request, body: SuperTaskBody) -> dict[str, Any]:
        return svc(request).super_submit(body.text)

    @app.post("/api/super/cancel")
    async def super_cancel(request: Request) -> dict[str, Any]:
        return await svc(request).super_cancel()

    @app.get("/api/super/tasks/{task_id}")
    async def super_task(request: Request, task_id: str) -> dict[str, Any]:
        return svc(request).super_task(task_id)

    @app.put("/api/super/workers/{worker}/limit")
    async def super_limit(request: Request, worker: str, body: SuperLimitBody) -> dict[str, Any]:
        return svc(request).super_set_limit(worker, body.limit)

    @app.post("/api/super/workers/{worker}/wake")
    async def super_wake(request: Request, worker: str) -> dict[str, Any]:
        return svc(request).super_clear_cooldown(worker)

    @app.get("/api/wander")
    async def wander_status(request: Request) -> dict[str, Any]:
        return svc(request).wander_status()

    @app.post("/api/wander/start")
    async def wander_start(request: Request, body: WanderStartBody) -> dict[str, Any]:
        return svc(request).wander_start(body.member, body.site)

    @app.post("/api/wander/stop")
    async def wander_stop(request: Request) -> dict[str, Any]:
        return svc(request).wander_stop()

    @app.get("/api/wander/log")
    async def wander_log(request: Request, member: str, after: int = 0) -> dict[str, Any]:
        return {"messages": svc(request).wander_log(member, after)}

    @app.get("/api/wander/frame")
    async def wander_frame(request: Request) -> Response:
        frame = svc(request).wander_frame()
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
        if frame is None:
            return Response(status_code=204, headers=headers)
        return Response(content=frame, media_type="image/jpeg", headers=headers)

    @app.post("/api/watch/adb/pair")
    async def watch_adb_pair(request: Request, body: AdbPairBody) -> dict[str, Any]:
        watch_local(request)
        return await asyncio.to_thread(svc(request).watch_adb_pair, body.host, body.port, body.code)

    @app.post("/api/watch/adb/connect")
    async def watch_adb_connect(request: Request, body: AdbConnectBody) -> dict[str, Any]:
        watch_local(request)
        return await asyncio.to_thread(svc(request).watch_adb_connect, body.host, body.port)

    @app.post("/api/watch/overlay")
    async def watch_overlay(request: Request) -> dict[str, Any]:
        watch_local(request)
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        return svc(request).watch_overlay(port)

    @app.post("/api/watch/adb/qr")
    async def watch_adb_qr(request: Request) -> dict[str, Any]:
        watch_local(request)
        return await asyncio.to_thread(svc(request).watch_adb_qr)

    @app.get("/api/watch/adb/qr")
    async def watch_adb_qr_poll(request: Request) -> dict[str, Any]:
        watch_local(request)
        return await asyncio.to_thread(svc(request).watch_adb_qr_poll)

    @app.post("/api/watch/scrcpy")
    async def watch_scrcpy(request: Request, body: WatchScrcpyBody) -> dict[str, Any]:
        watch_local(request)
        return await asyncio.to_thread(svc(request).watch_scrcpy, body.source)

    @app.get("/api/music/covers")
    async def music_covers(request: Request) -> dict[str, Any]:
        return svc(request).music_covers()

    @app.get("/api/music/covers/{name}")
    async def music_cover(request: Request, name: str) -> Response:
        data, mime = svc(request).music_cover(name)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
        return Response(content=data, media_type=mime, headers=headers)

    @app.get("/api/office")
    async def office(request: Request) -> dict[str, Any]:
        return svc(request).office()

    @app.post("/api/office/performance/reset")
    async def office_performance_reset(request: Request) -> dict[str, Any]:
        return svc(request).reset_performance()

    @app.get("/api/chat/rooms")
    async def chat_rooms(request: Request) -> dict[str, Any]:
        return svc(request).chat_rooms()

    @app.get("/api/chat")
    async def chat(
        request: Request, after: int = 0, q: str | None = None, room: str = "main"
    ) -> dict[str, Any]:
        return svc(request).chat_view(after, q, room)

    @app.post("/api/chat", status_code=202)
    async def chat_post(request: Request, body: ChatBody) -> dict[str, Any]:
        return await svc(request).chat_post(
            body.text, body.room, body.media, body.quote, files=body.files
        )

    @app.post("/api/chat/pat", status_code=202)
    async def chat_pat(request: Request, body: PatBody) -> dict[str, Any]:
        return await svc(request).chat_pat(body.member, body.room)

    @app.post("/api/chat/forward", status_code=202)
    async def chat_forward(request: Request, body: ForwardBody) -> dict[str, Any]:
        return await svc(request).chat_forward(body.id, body.room)

    @app.post("/api/chat/recall")
    async def chat_recall(request: Request, body: RecallBody) -> dict[str, Any]:
        return svc(request).chat_recall(body.id, body.room)

    @app.post("/api/chat/media", status_code=201)
    async def chat_media_upload(request: Request) -> dict[str, Any]:
        """聊天 / 朋友圈的图片、视频、语音：请求体是原始字节，Content-Type 是文件类型。"""
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > CHAT_MEDIA_MAX:
            raise ServiceError("文件太大", 413)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > CHAT_MEDIA_MAX:
                raise ServiceError("文件太大", 413)
            chunks.append(chunk)
        mime = request.headers.get("content-type", "")
        return svc(request).chat_media_save(b"".join(chunks), mime)

    @app.get("/api/chat/media/{name}")
    async def chat_media_file(request: Request, name: str) -> Response:
        data, mime = svc(request).chat_media_file(name)
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=86400, immutable",
            "Accept-Ranges": "bytes",
        }
        span = _byte_range(request.headers.get("range"), len(data))
        if span == "invalid":
            return Response(status_code=416, headers={"Content-Range": f"bytes */{len(data)}"})
        if span is not None:
            start, end = span
            headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
            return Response(
                content=data[start : end + 1], status_code=206, media_type=mime, headers=headers
            )
        return Response(content=data, media_type=mime, headers=headers)

    @app.post("/api/chat/messages/{message_id}/look", status_code=202)
    async def chat_look(request: Request, message_id: int, room: str = "main") -> dict[str, Any]:
        """「细看」：按精细模式重新整理这条消息里的图片 / 视频 / 语音，再请一位成员回应。"""
        return await svc(request).chat_look(room, message_id)

    @app.delete("/api/perception")
    async def perception_clear(request: Request) -> dict[str, Any]:
        """一键清空感官缓存（本机 data/perception/）。"""
        return svc(request).perception_clear()

    @app.post("/api/chat/handoff", status_code=202)
    async def chat_handoff(request: Request, body: HandoffBody) -> dict[str, Any]:
        return svc(request).chat_handoff(body.text)

    @app.post("/api/chat/handoff/{message_id}/start", status_code=202)
    async def chat_handoff_start(
        request: Request, message_id: int, room: str = "main"
    ) -> dict[str, Any]:
        """聊天里成员交办的事：你点了「交给办公室」才开工（docs/CHAT.md G14）。"""
        return svc(request).chat_handoff_start(room, message_id)

    @app.post("/api/chat/handoff/{message_id}/dismiss")
    async def chat_handoff_dismiss(
        request: Request, message_id: int, room: str = "main"
    ) -> dict[str, Any]:
        return svc(request).chat_handoff_dismiss(room, message_id)

    @app.get("/api/chat/voices")
    async def chat_voices(request: Request) -> dict[str, Any]:
        return svc(request).member_voices()

    @app.post("/api/chat/voices/{member}")
    async def chat_voice(request: Request, member: str, body: VoiceBody) -> Response:
        """用成员的固定音色念 text（省略时念一句试听）；返回音频，花费计入创作输出月上限。"""
        clip = await svc(request).member_voice(member, body.text)
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "X-Voice-Cost": f"{clip.cost_usd:.6f}",
            "X-Voice-Cached": "1" if clip.cached else "0",
        }
        if clip.seconds is not None:
            headers["X-Voice-Seconds"] = f"{clip.seconds:.2f}"
        return Response(content=clip.data, media_type=clip.mime, headers=headers)

    @app.get("/api/stickers")
    async def stickers(request: Request) -> dict[str, Any]:
        return svc(request).member_stickers()

    @app.delete("/api/stickers/stash")
    async def sticker_stash_clear(request: Request, member: str | None = None) -> dict[str, Any]:
        """清空成员从你那收来的表情包（member 省略时所有人）。"""
        return svc(request).stash_clear(member)

    @app.delete("/api/stickers/stash/{member}/{n}")
    async def sticker_stash_delete(request: Request, member: str, n: int) -> dict[str, Any]:
        return svc(request).stash_delete(member, n)

    @app.post("/api/stickers/{member}/{name}/draw")
    async def sticker_draw(request: Request, member: str, name: str) -> dict[str, Any]:
        """画（或重画）一张专属表情；新画的是「待审」。花费计入表情包与创作输出的月上限。"""
        return await svc(request).draw_sticker(member, name)

    @app.post("/api/stickers/{member}/{name}/review")
    async def sticker_review(
        request: Request, member: str, name: str, body: StickerReviewBody
    ) -> dict[str, Any]:
        return svc(request).review_sticker(member, name, body.status)

    @app.get("/api/stickers/{member}/{name}/image")
    async def sticker_image(request: Request, member: str, name: str) -> Response:
        data = svc(request).sticker_file(member, name)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"}
        mime = "image/gif" if data[:3] == b"GIF" else "image/png"
        return Response(content=data, media_type=mime, headers=headers)

    @app.get("/api/emoji")
    async def emoji_status(request: Request) -> dict[str, Any]:
        """网页里的 emoji 显示成哪套图。"""
        return svc(request).emoji_status()

    @app.get("/api/emoji/{code}.png")
    def emoji_image(request: Request, code: str) -> Response:
        """一个 emoji 的图（用到才下载到本机）；没有时 404，网页退回系统字体。"""
        data = svc(request).emoji_image(code)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=2592000"}
        return Response(content=data, media_type="image/png", headers=headers)

    @app.get("/api/sticker-packs")
    async def sticker_packs(request: Request) -> dict[str, Any]:
        """本机装的外部表情包（dsh-meme 格式）。"""
        return svc(request).sticker_packs()

    @app.get("/api/sticker-packs/catalog")
    def sticker_pack_catalog(request: Request) -> dict[str, Any]:
        """在线目录里的包（要联网）。"""
        return svc(request).sticker_pack_catalog()

    @app.post("/api/sticker-packs")
    def sticker_pack_install(request: Request, body: StickerPackBody) -> dict[str, Any]:
        """从在线目录装一个包（下载、核对 sha256）；owner 为空 = 通用。"""
        return svc(request).install_sticker_pack(body.id, body.owner, body.label or "")

    @app.patch("/api/sticker-packs/{pack_id}")
    async def sticker_pack_assign(
        request: Request, pack_id: str, body: StickerPackBody
    ) -> dict[str, Any]:
        return svc(request).assign_sticker_pack(pack_id, body.owner, body.label)

    @app.delete("/api/sticker-packs/{pack_id}")
    async def sticker_pack_remove(request: Request, pack_id: str) -> dict[str, Any]:
        return svc(request).remove_sticker_pack(pack_id)

    @app.get("/api/sticker-packs/{pack_id}/{n}")
    async def sticker_pack_image(request: Request, pack_id: str, n: int) -> Response:
        data, mime = svc(request).sticker_pack_image(pack_id, n)
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=2592000"}
        return Response(content=data, media_type=mime, headers=headers)

    @app.get("/api/chat/stickers")
    async def chat_stickers(request: Request) -> dict[str, Any]:
        """聊天里显示的成员专属表情（审过的那些）。"""
        return svc(request).chat_sticker_packs()

    @app.get("/api/chat/personas")
    async def chat_personas(request: Request) -> dict[str, Any]:
        return svc(request).chat_personas()

    @app.put("/api/chat/personas/{member}")
    async def chat_set_persona(request: Request, member: str, body: PersonaBody) -> dict[str, Any]:
        return svc(request).chat_set_persona(member, body.persona)

    @app.get("/api/chat/memories")
    async def chat_memories(request: Request, member: str | None = None) -> dict[str, Any]:
        return svc(request).chat_memories(member)

    @app.post("/api/chat/memories", status_code=201)
    async def chat_add_memory(request: Request, body: MemoryBody) -> dict[str, Any]:
        if not body.member:
            raise ServiceError("要写明是哪位成员的记忆")
        return svc(request).chat_add_memory(body.member, body.text)

    @app.post("/api/chat/memories/tidy")
    async def chat_tidy_memories(request: Request) -> dict[str, Any]:
        return await svc(request).chat_tidy_memories()

    @app.patch("/api/chat/memories/{memory_id}")
    async def chat_update_memory(
        request: Request, memory_id: int, body: MemoryBody
    ) -> dict[str, Any]:
        return svc(request).chat_update_memory(memory_id, body.text)

    @app.delete("/api/chat/memories/{memory_id}")
    async def chat_delete_memory(request: Request, memory_id: int) -> dict[str, Any]:
        return svc(request).chat_delete_memory(memory_id)

    @app.delete("/api/chat/memories")
    async def chat_clear_memories(request: Request, member: str | None = None) -> dict[str, Any]:
        return svc(request).chat_clear_memories(member)

    @app.get("/api/chat/memory/{member}/items")
    async def chat_memory_items(
        request: Request, member: str, q: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        return svc(request).chat_memory_items(member, q, limit)

    @app.get("/api/chat/memory/{member}/diary")
    async def chat_read_diary(request: Request, member: str, q: str = "") -> dict[str, Any]:
        return svc(request).chat_read_diary(member, q)

    @app.get("/api/chat/memory/items/{item_id}/history")
    async def chat_memory_item_history(request: Request, item_id: int) -> dict[str, Any]:
        return svc(request).chat_memory_item_history(item_id)

    @app.patch("/api/chat/memory/items/{item_id}")
    async def chat_update_memory_item(
        request: Request, item_id: int, body: MemoryBody
    ) -> dict[str, Any]:
        return svc(request).chat_update_memory_item(item_id, body.text)

    @app.delete("/api/chat/memory/items/{item_id}")
    async def chat_delete_memory_item(request: Request, item_id: int) -> dict[str, Any]:
        return svc(request).chat_delete_memory_item(item_id)

    @app.delete("/api/chat/memory/hanging/{hanging_id}")
    async def chat_drop_hanging(request: Request, hanging_id: int) -> dict[str, Any]:
        return svc(request).chat_drop_hanging(hanging_id)

    @app.get("/api/moments")
    async def moments(
        request: Request, author: str | None = None, before: int | None = None
    ) -> dict[str, Any]:
        return svc(request).moments_view(author, before)

    @app.post("/api/moments", status_code=201)
    async def moments_post(request: Request, body: MomentBody) -> dict[str, Any]:
        return await svc(request).moments_post(body.text, body.media)

    @app.post("/api/moments/{moment_id}/like")
    async def moments_like(request: Request, moment_id: int) -> dict[str, Any]:
        return svc(request).moments_like(moment_id)

    @app.post("/api/moments/{moment_id}/comments", status_code=201)
    async def moments_comment(
        request: Request, moment_id: int, body: MomentCommentBody
    ) -> dict[str, Any]:
        return await svc(request).moments_comment(moment_id, body.text, body.reply_to)

    @app.delete("/api/moments/{moment_id}")
    async def moments_delete(request: Request, moment_id: int) -> dict[str, Any]:
        return svc(request).moments_delete(moment_id)

    @app.delete("/api/moments/comments/{comment_id}")
    async def moments_delete_comment(request: Request, comment_id: int) -> dict[str, Any]:
        return svc(request).moments_delete_comment(comment_id)

    # 成员引擎：自我档案（docs/MEMBER_ENGINE.md）
    @app.get("/api/chat/profiles")
    async def member_profiles(request: Request) -> dict[str, Any]:
        return svc(request).member_profiles()

    @app.post("/api/chat/profiles/import")
    async def member_profile_import(request: Request, body: ProfileImportBody) -> dict[str, Any]:
        return svc(request).member_profile_import(body.text, body.mapping, body.dry_run)

    @app.get("/api/chat/profiles/{member}")
    async def member_profile(request: Request, member: str) -> dict[str, Any]:
        return svc(request).member_profile(member)

    @app.patch("/api/chat/profiles/{member}")
    async def member_profile_change(
        request: Request, member: str, body: ProfilePatchBody
    ) -> dict[str, Any]:
        return svc(request).member_profile_change(member, body.patch, body.reason)

    @app.post("/api/chat/profiles/{member}/lock")
    async def member_profile_lock(
        request: Request, member: str, body: ProfileLockBody
    ) -> dict[str, Any]:
        return svc(request).member_profile_lock(member, body.path, body.locked)

    @app.post("/api/chat/profiles/{member}/revert")
    async def member_profile_revert(
        request: Request, member: str, body: ProfileRevertBody
    ) -> dict[str, Any]:
        return svc(request).member_profile_revert(member, body.version)

    @app.delete("/api/chat/profiles/{member}")
    async def member_profile_clear(request: Request, member: str) -> dict[str, Any]:
        return svc(request).member_profile_clear(member)

    # 成员成长（P6）：时间线、马上反思上一周、撤回一条、清空时间线
    @app.get("/api/chat/growth/{member}")
    async def member_growth(request: Request, member: str) -> dict[str, Any]:
        return svc(request).member_growth(member)

    @app.post("/api/chat/growth/{member}/run")
    async def member_growth_run(request: Request, member: str) -> dict[str, Any]:
        return await svc(request).member_growth_run(member)

    @app.post("/api/chat/growth/{member}/revert")
    async def member_growth_revert(
        request: Request, member: str, body: GrowthRevertBody
    ) -> dict[str, Any]:
        return svc(request).member_growth_revert(member, body.id)

    @app.post("/api/chat/growth/{member}/undo")
    async def member_growth_undo(
        request: Request, member: str, body: GrowthUndoBody
    ) -> dict[str, Any]:
        return svc(request).member_growth_undo(member, body.token)

    @app.delete("/api/chat/growth/{member}")
    async def member_growth_clear(request: Request, member: str) -> dict[str, Any]:
        return svc(request).member_growth_clear(member)

    # 成员关系（P4）：和你的阶段、同事之间的亲近度与看法；清空
    @app.get("/api/chat/relation/{member}")
    async def member_relation(request: Request, member: str) -> dict[str, Any]:
        return svc(request).member_relation(member)

    @app.post("/api/chat/relation/{member}/insight")
    async def member_relation_insight(request: Request, member: str) -> dict[str, Any]:
        return await svc(request).member_relation_insight(member)

    @app.delete("/api/chat/relation/{member}/views/{index}")
    async def member_relation_drop_view(
        request: Request, member: str, index: int
    ) -> dict[str, Any]:
        return svc(request).member_relation_drop_view(member, index)

    @app.put("/api/chat/relation/{member}/views/{index}/reply")
    async def member_relation_reply_view(
        request: Request, member: str, index: int, body: ViewReplyBody
    ) -> dict[str, Any]:
        return svc(request).member_relation_reply_view(member, index, body.text)

    @app.delete("/api/chat/relation")
    async def member_relation_clear(request: Request, member: str | None = None) -> dict[str, Any]:
        return svc(request).member_relation_clear(member)

    @app.get("/api/chat/engine/state")
    async def member_states(request: Request) -> dict[str, Any]:
        return svc(request).member_states()

    @app.delete("/api/chat/engine/state")
    async def member_states_clear(request: Request, member: str | None = None) -> dict[str, Any]:
        return svc(request).member_states_clear(member)

    # 关于你（成员系统 P8）：8 位共用的用户画像
    @app.get("/api/chat/about")
    async def about_view(request: Request) -> dict[str, Any]:
        return svc(request).about_view()

    @app.post("/api/chat/about/facts", status_code=201)
    async def about_add(request: Request, body: AboutFactBody) -> dict[str, Any]:
        return svc(request).about_add(body.text or "", body.domain)

    @app.patch("/api/chat/about/facts/{fact_id}")
    async def about_edit(request: Request, fact_id: int, body: AboutFactBody) -> dict[str, Any]:
        return svc(request).about_edit(fact_id, body.text, body.domain)

    @app.delete("/api/chat/about/facts/{fact_id}")
    async def about_delete(request: Request, fact_id: int) -> dict[str, Any]:
        return svc(request).about_delete(fact_id)

    @app.post("/api/chat/about/facts/{fact_id}/reject")
    async def about_reject(request: Request, fact_id: int) -> dict[str, Any]:
        return svc(request).about_reject(fact_id)

    @app.post("/api/chat/about/facts/{fact_id}/lock")
    async def about_lock(request: Request, fact_id: int, body: AboutLockBody) -> dict[str, Any]:
        return svc(request).about_lock(fact_id, body.locked)

    @app.delete("/api/chat/about/rejected/{index}")
    async def about_forget_rejected(request: Request, index: int) -> dict[str, Any]:
        return svc(request).about_forget_rejected(index)

    @app.post("/api/chat/about/domains")
    async def about_domain(request: Request, body: AboutDomainBody) -> dict[str, Any]:
        return svc(request).about_domain(body.domain, body.on)

    @app.delete("/api/chat/about")
    async def about_clear(request: Request, rejected: bool = False) -> dict[str, Any]:
        return svc(request).about_clear(rejected)

    @app.get("/api/chat/about/export")
    async def about_export(request: Request, fmt: str = "json") -> Response:
        data, media, name = svc(request).about_export(fmt)
        return Response(
            data,
            media_type=media,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    # 关于你 · 你的状态（P8 第二块；每项默认关）
    @app.get("/api/chat/about/status")
    async def about_status(request: Request) -> dict[str, Any]:
        return svc(request).about_status()

    @app.post("/api/chat/about/status")
    async def about_status_set(request: Request, body: AboutStatusBody) -> dict[str, Any]:
        return svc(request).about_status_set(body.kind, body.on)

    @app.post("/api/chat/about/status/player")
    async def about_music_player(request: Request, body: AboutPlayerBody) -> dict[str, Any]:
        return svc(request).about_music_player(body.player)

    @app.post("/api/chat/about/games")
    async def about_game_add(request: Request, body: AboutGameBody) -> dict[str, Any]:
        return svc(request).about_game_add(body.exe, body.name)

    @app.delete("/api/chat/about/games")
    async def about_game_del(request: Request, exe: str) -> dict[str, Any]:
        return svc(request).about_game_del(exe)

    @app.post("/api/chat/about/schedule/classes")
    async def about_class_add(request: Request, body: AboutClassBody) -> dict[str, Any]:
        return svc(request).about_class_add(body.weekday, body.start, body.end, body.name)

    @app.post("/api/chat/about/schedule/ddls")
    async def about_ddl_add(request: Request, body: AboutDdlBody) -> dict[str, Any]:
        return svc(request).about_ddl_add(body.name, body.due)

    @app.delete("/api/chat/about/schedule/{kind}/{item_id}")
    async def about_schedule_del(request: Request, kind: str, item_id: int) -> dict[str, Any]:
        return svc(request).about_schedule_del(kind, item_id)

    @app.delete("/api/chat/about/schedule")
    async def about_schedule_clear(request: Request) -> dict[str, Any]:
        return svc(request).about_schedule_clear()

    @app.get("/api/chat/engine/events")
    async def member_events_count(request: Request) -> dict[str, Any]:
        return svc(request).member_events_count()

    @app.delete("/api/chat/engine/events")
    async def member_events_clear(request: Request) -> dict[str, Any]:
        return svc(request).member_events_clear()

    @app.get("/api/life/reports")
    async def life_reports(request: Request) -> dict[str, Any]:
        return svc(request).life_reports()

    @app.get("/api/chat/feeds")
    async def chat_feeds(request: Request) -> dict[str, Any]:
        return svc(request).chat_feeds()

    @app.put("/api/chat/feeds")
    async def chat_set_feeds(request: Request, body: FeedsBody) -> dict[str, Any]:
        return svc(request).chat_set_feeds(body.feeds)

    @app.get("/api/chat/memes")
    async def chat_memes(request: Request) -> dict[str, Any]:
        return svc(request).chat_memes()

    @app.post("/api/chat/memes")
    async def chat_add_meme(request: Request, body: MemeBody) -> dict[str, Any]:
        return svc(request).chat_add_meme(body.model_dump())

    @app.delete("/api/chat/memes")
    async def chat_remove_meme(request: Request, phrase: str) -> dict[str, Any]:
        return svc(request).chat_remove_meme(phrase)

    @app.post("/api/chat/memes/refresh")
    async def chat_refresh_memes(request: Request) -> dict[str, Any]:
        return await svc(request).chat_refresh_memes()

    @app.post("/api/chat/feeds/refresh")
    async def chat_refresh_feeds(request: Request) -> dict[str, Any]:
        return await svc(request).chat_refresh_feeds()

    # 办公室的资料浏览器：打开网页（只读抓取正文）、搜索、存资料、提问时附上
    @app.get("/api/clips")
    async def clips(request: Request) -> dict[str, Any]:
        return svc(request).clips()

    @app.post("/api/clips/open")
    async def clip_open(request: Request, body: ClipOpenBody) -> dict[str, Any]:
        return await svc(request).clip_open(body.url)

    @app.post("/api/clips/search")
    async def clip_search(request: Request, body: ClipSearchBody) -> dict[str, Any]:
        return await svc(request).clip_search(body.query)

    @app.post("/api/clips", status_code=201)
    async def clip_save(request: Request, body: ClipSaveBody) -> dict[str, Any]:
        return svc(request).clip_save(
            body.url, body.title, body.text, partial=body.partial, note=body.note
        )

    @app.get("/api/clips/{clip_id}")
    async def clip_text(request: Request, clip_id: str) -> dict[str, Any]:
        return svc(request).clip_text(clip_id)

    @app.delete("/api/clips/{clip_id}")
    async def clip_delete(request: Request, clip_id: str) -> dict[str, Any]:
        return svc(request).clip_delete(clip_id)

    @app.post("/api/clips/{clip_id}/attach", status_code=201)
    async def clip_attach(request: Request, clip_id: str) -> dict[str, Any]:
        return svc(request).clip_attach(clip_id)

    @app.delete("/api/chat")
    async def chat_clear(request: Request, room: str = "main") -> dict[str, Any]:
        return svc(request).chat_clear(room)

    @app.get("/api/evolution")
    async def evolution(request: Request) -> dict[str, Any]:
        return svc(request).evolution()

    @app.post("/api/evolution/meetings", status_code=202)
    async def evolution_meet(request: Request, body: MeetBody | None = None) -> dict[str, Any]:
        body = body or MeetBody()
        return svc(request).evolution_meet(kind=body.kind, theme=body.theme)

    @app.put("/api/evolution/next-theme")
    async def evolution_theme(request: Request, body: ThemeBody) -> dict[str, Any]:
        return svc(request).evolution_set_theme(body.theme)

    @app.post("/api/evolution/meetings/{session_id}/export")
    async def evolution_export(request: Request, session_id: str) -> dict[str, Any]:
        return svc(request).evolution_export(session_id)

    @app.post("/api/evolution/meetings/{session_id}/plugins")
    async def evolution_plugins(request: Request, session_id: str) -> dict[str, Any]:
        return await svc(request).evolution_plugins(session_id)

    @app.post("/api/evolution/scout")
    async def evolution_scout(request: Request) -> dict[str, Any]:
        return await svc(request).evolution_scout()

    @app.post("/api/evolution/meetings/{session_id}/ideas/{idea_id}")
    async def evolution_decide(
        request: Request, session_id: str, idea_id: str, body: DecisionBody
    ) -> dict[str, Any]:
        return svc(request).evolution_decide(session_id, idea_id, body.decision)

    @app.post("/api/evolution/meetings/{session_id}/ideas/{idea_id}/merge")
    async def evolution_merge(request: Request, session_id: str, idea_id: str) -> dict[str, Any]:
        return await svc(request).evolution_merge(session_id, idea_id)

    @app.get("/api/apartment")
    async def apartment(request: Request, hour: int, weekday: int) -> dict[str, Any]:
        return svc(request).apartment(hour, weekday)

    @app.get("/api/plugins/board")
    async def plugin_board(request: Request) -> dict[str, Any]:
        return svc(request).plugin_board()

    @app.get("/api/changelog")
    async def changelog(request: Request) -> dict[str, Any]:
        return svc(request).changelog()

    @app.get("/api/home-tz")
    async def home_tz(request: Request) -> dict[str, Any]:
        return svc(request).home_tz()

    @app.put("/api/home-tz")
    async def set_home_tz(request: Request, body: HomeTzBody) -> dict[str, Any]:
        return svc(request).set_home_tz(body.zone)

    @app.get("/api/calendar")
    async def calendar(
        request: Request, days: int | None = None, start: str | None = None
    ) -> dict[str, Any]:
        return svc(request).calendar_view(days, start)

    @app.post("/api/calendar/dates")
    async def calendar_add_date(request: Request, body: CalendarDateBody) -> dict[str, Any]:
        return svc(request).calendar_add_date(body.name, body.date, body.lunar)

    @app.delete("/api/calendar/dates/{date_id}")
    async def calendar_remove_date(request: Request, date_id: str) -> dict[str, Any]:
        return svc(request).calendar_remove_date(date_id)

    @app.get("/api/mail")
    async def mail(request: Request) -> dict[str, Any]:
        return svc(request).mail_view()

    @app.put("/api/mail/settings")
    async def mail_settings(request: Request, body: MailSettingsBody) -> dict[str, Any]:
        return svc(request).mail_settings(**body.model_dump())

    @app.post("/api/mail/login")
    async def mail_login(request: Request) -> dict[str, Any]:
        return svc(request).mail_login()

    @app.post("/api/mail/refresh")
    async def mail_refresh(request: Request, body: MailRefreshBody) -> dict[str, Any]:
        return svc(request).mail_refresh(manual=body.manual)

    @app.get("/api/mail/messages/{message_id:path}")
    async def mail_message(request: Request, message_id: str) -> dict[str, Any]:
        return svc(request).mail_message(message_id)

    @app.post("/api/mail/reminders/{reminder_id}")
    async def mail_reminder(
        request: Request, reminder_id: int, body: MailReminderBody
    ) -> dict[str, Any]:
        return svc(request).mail_reminder(reminder_id, body.action)

    @app.post("/api/mail/mark")
    async def mail_mark(request: Request, body: MailMarkBody) -> dict[str, Any]:
        return svc(request).mail_mark(body.id, body.mark)

    @app.post("/api/mail/category")
    async def mail_category(request: Request, body: MailCategoryBody) -> dict[str, Any]:
        return svc(request).mail_category(body.id, body.category)

    @app.post("/api/mail/images")
    async def mail_images(request: Request, body: MailIdBody) -> dict[str, Any]:
        return svc(request).mail_images(body.id)

    @app.post("/api/mail/ai/{kind}")
    async def mail_ai(
        request: Request, kind: Literal["summary", "translate", "digest"], body: MailAIBody
    ) -> dict[str, Any]:
        return await svc(request).mail_ai_do(kind, body.id, again=body.again)

    @app.post("/api/mail/logout")
    async def mail_logout(request: Request) -> dict[str, Any]:
        return svc(request).mail_logout()

    @app.delete("/api/mail/cache")
    async def mail_clear(request: Request) -> dict[str, Any]:
        return svc(request).mail_clear()

    @app.get("/api/models/candidates")
    async def model_candidates(request: Request) -> dict[str, Any]:
        return svc(request).model_candidates()

    @app.post("/api/models/candidates/refresh")
    async def refresh_model_candidates(request: Request) -> dict[str, Any]:
        return await svc(request).refresh_model_candidates()

    @app.get("/api/models/directory")
    async def model_directory(request: Request) -> dict[str, Any]:
        return svc(request).model_directory()

    @app.post("/api/models/directory/refresh")
    async def refresh_model_directory(request: Request) -> dict[str, Any]:
        return await svc(request).refresh_model_directory()

    @app.get("/api/sessions/{session_id}/payslip")
    async def payslip(request: Request, session_id: str) -> dict[str, Any]:
        return svc(request).payslip(session_id)

    @app.get("/api/contributions")
    async def contributions(request: Request) -> list[dict[str, Any]]:
        return svc(request).contributions()

    @app.get("/api/sessions")
    async def sessions(request: Request, limit: int = 50) -> list[dict[str, Any]]:
        return svc(request).sessions(min(max(limit, 1), 200))

    @app.post("/api/sessions", status_code=202)
    async def create(request: Request, body: CreateSession) -> dict[str, str]:
        sid = svc(request).create(
            body.question,
            tier=body.tier,
            models=body.models,
            coordinator=body.coordinator,
            anonymous=body.anonymous,
            workflow=body.workflow,
            seed=body.seed,
            attachments=body.attachments,
            media=body.media,
            media_tier=body.media_tier,
            style_refs=body.style_refs,
            carry_from=body.carry_from,
        )
        return {"session_id": sid}

    @app.get("/api/sessions/{session_id}/carry")
    async def carry(request: Request, session_id: str) -> dict[str, Any]:
        """接力用：上一场的题目和将带入下一场的成果摘要。"""
        return svc(request).carry(session_id)

    @app.get("/api/sessions/{session_id}/files/{file_id}")
    async def file_download(request: Request, session_id: str, file_id: str) -> Response:
        """下载成员生成的文件。始终作为附件下载（不在页面中打开），图片可以 ?inline=1 显示。"""
        data, row = svc(request).file_content(session_id, file_id)
        name = row["path"].rsplit("/", 1)[-1]
        playable = row["mime"] in INLINE_MEDIA
        inline = request.query_params.get("inline") == "1" and (
            row["mime"] in INLINE_IMAGES or playable
        )
        disposition = "inline" if inline else "attachment"
        headers = {
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        }
        media = row["mime"] if inline else "application/octet-stream"
        if inline and playable:
            headers["Accept-Ranges"] = "bytes"
            span = _byte_range(request.headers.get("range"), len(data))
            if span == "invalid":
                return Response(status_code=416, headers={"Content-Range": f"bytes */{len(data)}"})
            if span is not None:
                start, end = span
                headers["Content-Range"] = f"bytes {start}-{end}/{len(data)}"
                return Response(
                    content=data[start : end + 1],
                    status_code=206,
                    media_type=media,
                    headers=headers,
                )
        return Response(content=data, media_type=media, headers=headers)

    @app.get("/api/export/options")
    async def export_options(request: Request) -> dict[str, Any]:
        """导出面板的可选项；保存文件夹只告诉本机（平板看不到电脑上的路径）。"""
        data = svc(request).export_options()
        data["can_save_to_folder"] = not is_remote(_client(request.scope))
        if not data["can_save_to_folder"]:
            data["folder"] = data["suggested_folder"] = ""
        return data

    @app.get("/api/sessions/{session_id}/export")
    async def export_document(
        request: Request,
        session_id: str,
        format: str = "docx",
        size: str | None = None,
        theme: str | None = None,
        ratio: str | None = None,
        question: bool | None = None,
        process: bool | None = None,
    ) -> Response:
        """导出最终成果：md / docx / pptx / pdf / html，可选字号、配色、PPT 比例、附题目 / 过程。

        本地转换，不调用模型。"""
        options = {
            "size": size, "theme": theme, "ratio": ratio, "question": question, "process": process
        }  # fmt: skip
        data, name, mime = svc(request).export_document(session_id, format, options)
        headers = {
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Cache-Control": "no-store",
        }
        return Response(content=data, media_type=mime, headers=headers)

    @app.post("/api/sessions/{session_id}/export/save")
    async def export_save(request: Request, session_id: str, body: ExportSave) -> dict[str, Any]:
        """直接存进电脑上的文件夹。只允许在电脑本机上用，平板 / 手机不能往电脑里写文件。"""
        if is_remote(_client(request.scope)):
            raise ServiceError("只能在电脑上直接存到文件夹；平板上请用下载", 403)
        options = body.model_dump(exclude={"format", "folder"})
        return svc(request).export_save(session_id, body.format, body.folder, options)

    @app.get("/api/sessions/{session_id}/files/{file_id}/preview")
    async def file_preview(request: Request, session_id: str, file_id: str) -> dict[str, Any]:
        return svc(request).file_preview(session_id, file_id)

    @app.post("/api/estimate")
    async def estimate(request: Request, body: EstimateRequest) -> dict[str, Any]:
        """提交前预估（不调用模型、不产生费用）。提交时带上返回的 seed，阵容与预估一致。"""
        return svc(request).estimate(
            body.question,
            tier=body.tier,
            models=body.models,
            coordinator=body.coordinator,
            workflow=body.workflow,
            anonymous=body.anonymous,
            attachments=body.attachments,
            seed=body.seed,
            media=body.media,
            media_tier=body.media_tier,
            carry_from=body.carry_from,
        )

    @app.post("/api/uploads", status_code=201)
    async def upload(request: Request, name: str) -> dict[str, Any]:
        """上传一个文件：请求体是文件的原始字节，?name= 为文件名（只用于显示和判断扩展名）。"""
        service = svc(request)
        limit = service.max_upload_bytes
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > limit:
            raise ServiceError("文件超过大小上限", 413)
        chunks, size = [], 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > limit:
                raise ServiceError("文件超过大小上限", 413)
            chunks.append(chunk)
        return service.upload(name, b"".join(chunks))

    @app.get("/api/sessions/{session_id}")
    async def session(request: Request, session_id: str) -> dict[str, Any]:
        return svc(request).session(session_id)

    @app.post("/api/sessions/{session_id}/respond", status_code=202)
    async def respond(request: Request, session_id: str, body: Respond) -> dict[str, str]:
        svc(request).respond(session_id, body.response, body.note)
        return {"session_id": session_id}

    @app.post("/api/sessions/{session_id}/notes", status_code=201)
    async def add_note(request: Request, session_id: str, body: Note) -> dict[str, Any]:
        return svc(request).add_note(session_id, body.text)

    @app.post("/api/sessions/{session_id}/resume", status_code=202)
    async def resume(request: Request, session_id: str) -> dict[str, str]:
        svc(request).resume(session_id)
        return {"session_id": session_id}

    @app.post("/api/sessions/{session_id}/reveal")
    async def reveal(request: Request, session_id: str) -> dict[str, Any]:
        return svc(request).reveal_identities(session_id)

    @app.get("/api/sessions/{session_id}/events")
    async def events(request: Request, session_id: str) -> StreamingResponse:
        service_ = svc(request)
        service_.session(session_id)  # 不存在时先返回 404

        async def stream() -> AsyncIterator[str]:
            async for event in service_.events(session_id):
                if await request.is_disconnected():
                    break
                yield sse(event)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ---- 局域网访问（平板 / 手机）：设置只能在电脑本机上做 ----

    def local_only(request: Request) -> None:
        if is_remote(_client(request.scope)):
            raise ServiceError("只能在电脑上设置", 403)

    @app.get("/api/lan")
    async def lan_status(request: Request) -> dict[str, Any]:
        local_only(request)
        port = (request.scope.get("server") or ("", 8000))[1] or 8000
        return lan.status(int(port))

    @app.post("/api/lan/password")
    async def lan_password(request: Request, body: LanPassword) -> dict[str, Any]:
        local_only(request)
        try:
            lan.set_password(body.password)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        return {"ok": True}

    @app.post("/api/lan/signout")
    async def lan_signout(request: Request) -> dict[str, Any]:
        local_only(request)
        lan.sign_out_all()
        return {"ok": True}

    @app.post("/api/lan/pair")
    async def lan_pair(request: Request, body: LanPair) -> dict[str, Any]:
        local_only(request)
        port = (request.scope.get("server") or ("", 8000))[1] or 8000
        info = lan.status(int(port))
        if body.base not in info["urls"]:
            raise ServiceError("地址不是这台电脑的局域网地址")
        try:
            code = lan.new_pair_code()
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        link = f"{body.base}/lan/pair?c={quote(code)}"
        return {"url": link, "qr": qr_svg(link), "minutes": info["pair_minutes"]}

    @app.post("/api/lan/login")
    async def lan_login(request: Request, body: LanPassword) -> Response:
        result = lan.login(body.password, _client(request.scope) or "")
        if not result.ok:
            if result.reason == "locked":
                msg = f"输错次数太多，请 {result.retry_after} 秒后再试"
                headers = {"Retry-After": str(result.retry_after)}
                return JSONResponse({"detail": msg}, status_code=429, headers=headers)
            if result.reason == "no_password":
                return JSONResponse({"detail": "电脑上还没设访问口令"}, status_code=403)
            return JSONResponse({"detail": "口令不对"}, status_code=401)
        resp = JSONResponse({"ok": True})
        _lan_cookie(resp, result.token or "")
        return resp

    @app.post("/api/lan/logout")
    async def lan_logout() -> Response:
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(LAN_COOKIE, path="/")
        return resp

    # ---- 整库备份（docs/BACKUP.md）：恢复会覆盖全部数据，只能在电脑上做 ----

    @app.get("/api/backup")
    async def backup_status(request: Request) -> dict[str, Any]:
        local_only(request)
        return svc(request).backup_status()

    @app.post("/api/backup/checkpoint")
    async def backup_checkpoint(request: Request, body: BackupPoint) -> dict[str, Any]:
        local_only(request)
        return svc(request).backup_checkpoint(body.label)  # 同一连接，不放到别的线程

    @app.post("/api/backup/restore")
    async def backup_restore(request: Request, body: BackupRestore) -> dict[str, Any]:
        local_only(request)
        return svc(request).backup_restore(body.name)

    @app.get("/lan/pair")
    async def lan_pair_open(c: str = "") -> Response:
        token = lan.redeem_pair_code(c)
        if token is None:
            return RedirectResponse("/login.html?expired=1", status_code=303)
        resp = RedirectResponse("/", status_code=303)
        _lan_cookie(resp, token)
        return resp

    if WEB_DIR.is_dir():  # 静态前端（web/）
        app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
