"""FastAPI 应用：HTTP 接口 + SSE 实时推送。只调用 core.service。

启动：uvicorn roundtable.api.app:app --reload   （默认只监听本机）
"""

from __future__ import annotations

import asyncio
import json
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


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class ChatBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = ""
    room: str = "main"
    media: list[dict[str, Any]] = Field(default_factory=list)  # POST /api/chat/media 返回的 id
    quote: int | None = None  # 引用的消息 id


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


class FeedsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feeds: list[dict[str, Any]] | None = None  # None：恢复为配置文件里的


class MemoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    member: str | None = None  # 新建时必填


class HomeTzBody(BaseModel):
    """主时区：IANA 名字（如 Europe/London）或 local（跟随电脑）。"""

    model_config = ConfigDict(extra="forbid")

    zone: str = Field(max_length=64)


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


class WatchSayBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=1000)


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
        return await svc(request).chat_post(body.text, body.room, body.media, body.quote)

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
