"""FastAPI 应用：HTTP 接口 + SSE 实时推送。只调用 core.service。

启动：uvicorn roundtable.api.app:app --reload   （默认只监听本机）
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from roundtable.core.service import (
    CHAT_MEDIA_MAX,
    MAX_QUESTION_CHARS,
    RoundtableService,
    ServiceError,
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
    media: str | None = None  # 讨论模式的输出类型：image / speech / video
    media_tier: str | None = None  # 媒体模型的档位：budget / flagship
    # 作为风格参考的图片附件 id；省略 = 全部图片都是风格参考，[] = 都不是
    style_refs: list[str] | None = None


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


class FeedsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feeds: list[dict[str, Any]] | None = None  # None：恢复为配置文件里的


class MemoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    member: str | None = None  # 新建时必填


class MailSettingsBody(BaseModel):
    """邮件栏设置：只改给了的字段。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    rules: dict[str, Any] | None = None
    days: int | None = None
    show_window: bool | None = None
    extra_hosts: list[str] | None = None


class MailRefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manual: bool = True


class MailReminderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(accept|dismiss|undo)$")


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


def create_app(service: RoundtableService | None = None) -> FastAPI:
    """service 为空时在启动时根据 .env 与配置构建（测试中传入使用 Fake 模型的服务）。"""

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
        try:
            yield
        finally:
            await app.state.service.aclose()

    app = FastAPI(title="Roundtable", lifespan=lifespan)

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
        return await svc(request).chat_post(body.text, body.room, body.media)

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

    @app.get("/api/chat/feeds")
    async def chat_feeds(request: Request) -> dict[str, Any]:
        return svc(request).chat_feeds()

    @app.put("/api/chat/feeds")
    async def chat_set_feeds(request: Request, body: FeedsBody) -> dict[str, Any]:
        return svc(request).chat_set_feeds(body.feeds)

    @app.post("/api/chat/feeds/refresh")
    async def chat_refresh_feeds(request: Request) -> dict[str, Any]:
        return await svc(request).chat_refresh_feeds()

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
        )
        return {"session_id": sid}

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

    if WEB_DIR.is_dir():  # 静态前端（web/）
        app.mount("/", WebFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
