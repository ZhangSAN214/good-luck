"""FastAPI 应用：HTTP 接口 + SSE 实时推送。只调用 core.service。

启动：uvicorn roundtable.api.app:app --reload   （默认只监听本机）
"""

from __future__ import annotations

import json
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
    MAX_QUESTION_CHARS,
    RoundtableService,
    ServiceError,
)

WEB_DIR = Path(__file__).resolve().parents[3] / "web"
# 可以在页面里直接显示的图片类型（SVG 不在其中：只作为文本预览或下载）
INLINE_IMAGES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


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


class Respond(BaseModel):
    model_config = ConfigDict(extra="forbid")

    response: str
    note: str | None = None


def sse(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event['type']}\ndata: {data}\n\n"


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
        )
        return {"session_id": sid}

    @app.get("/api/sessions/{session_id}/files/{file_id}")
    async def file_download(request: Request, session_id: str, file_id: str) -> Response:
        """下载成员生成的文件。始终作为附件下载（不在页面中打开），图片可以 ?inline=1 显示。"""
        data, row = svc(request).file_content(session_id, file_id)
        name = row["path"].rsplit("/", 1)[-1]
        inline = request.query_params.get("inline") == "1" and row["mime"] in INLINE_IMAGES
        disposition = "inline" if inline else "attachment"
        headers = {
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Cache-Control": "private, max-age=3600",
        }
        media = row["mime"] if inline else "application/octet-stream"
        return Response(content=data, media_type=media, headers=headers)

    @app.get("/api/sessions/{session_id}/files/{file_id}/preview")
    async def file_preview(request: Request, session_id: str, file_id: str) -> dict[str, Any]:
        return svc(request).file_preview(session_id, file_id)

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
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app


app = create_app()
