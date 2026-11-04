"""办公室：名册与工资单、资料浏览器、革新例会、插件榜、模型图鉴、会话（提交、确认、恢复、揭晓、实时事件）、导出、预估、上传。

从 app.py 拆出（代码精简第 2 批）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from roundtable.api.bodies import (
    ClipOpenBody,
    ClipSaveBody,
    ClipSearchBody,
    CreateSession,
    DecisionBody,
    EstimateRequest,
    ExportSave,
    MeetBody,
    Note,
    Respond,
    ThemeBody,
)
from roundtable.api.common import INLINE_IMAGES, INLINE_MEDIA, _byte_range, _client, sse, svc
from roundtable.core.service import ServiceError, is_remote

router = APIRouter()


@router.get("/api/office")
async def office(request: Request) -> dict[str, Any]:
    return svc(request).office()


@router.post("/api/office/performance/reset")
async def office_performance_reset(request: Request) -> dict[str, Any]:
    return svc(request).reset_performance()


# 办公室的资料浏览器：打开网页（只读抓取正文）、搜索、存资料、提问时附上
@router.get("/api/clips")
async def clips(request: Request) -> dict[str, Any]:
    return svc(request).clips()


@router.post("/api/clips/open")
async def clip_open(request: Request, body: ClipOpenBody) -> dict[str, Any]:
    return await svc(request).clip_open(body.url)


@router.post("/api/clips/search")
async def clip_search(request: Request, body: ClipSearchBody) -> dict[str, Any]:
    return await svc(request).clip_search(body.query)


@router.post("/api/clips", status_code=201)
async def clip_save(request: Request, body: ClipSaveBody) -> dict[str, Any]:
    return svc(request).clip_save(
        body.url, body.title, body.text, partial=body.partial, note=body.note
    )


@router.get("/api/clips/{clip_id}")
async def clip_text(request: Request, clip_id: str) -> dict[str, Any]:
    return svc(request).clip_text(clip_id)


@router.delete("/api/clips/{clip_id}")
async def clip_delete(request: Request, clip_id: str) -> dict[str, Any]:
    return svc(request).clip_delete(clip_id)


@router.post("/api/clips/{clip_id}/attach", status_code=201)
async def clip_attach(request: Request, clip_id: str) -> dict[str, Any]:
    return svc(request).clip_attach(clip_id)


@router.get("/api/evolution")
async def evolution(request: Request) -> dict[str, Any]:
    return svc(request).evolution()


@router.post("/api/evolution/meetings", status_code=202)
async def evolution_meet(request: Request, body: MeetBody | None = None) -> dict[str, Any]:
    body = body or MeetBody()
    return svc(request).evolution_meet(kind=body.kind, theme=body.theme)


@router.put("/api/evolution/next-theme")
async def evolution_theme(request: Request, body: ThemeBody) -> dict[str, Any]:
    return svc(request).evolution_set_theme(body.theme)


@router.post("/api/evolution/meetings/{session_id}/export")
async def evolution_export(request: Request, session_id: str) -> dict[str, Any]:
    return svc(request).evolution_export(session_id)


@router.post("/api/evolution/meetings/{session_id}/plugins")
async def evolution_plugins(request: Request, session_id: str) -> dict[str, Any]:
    return await svc(request).evolution_plugins(session_id)


@router.post("/api/evolution/scout")
async def evolution_scout(request: Request) -> dict[str, Any]:
    return await svc(request).evolution_scout()


@router.post("/api/evolution/meetings/{session_id}/ideas/{idea_id}")
async def evolution_decide(
    request: Request, session_id: str, idea_id: str, body: DecisionBody
) -> dict[str, Any]:
    return svc(request).evolution_decide(session_id, idea_id, body.decision)


@router.post("/api/evolution/meetings/{session_id}/ideas/{idea_id}/merge")
async def evolution_merge(request: Request, session_id: str, idea_id: str) -> dict[str, Any]:
    return await svc(request).evolution_merge(session_id, idea_id)


@router.get("/api/plugins/board")
async def plugin_board(request: Request) -> dict[str, Any]:
    return svc(request).plugin_board()


@router.get("/api/models/candidates")
async def model_candidates(request: Request) -> dict[str, Any]:
    return svc(request).model_candidates()


@router.post("/api/models/candidates/refresh")
async def refresh_model_candidates(request: Request) -> dict[str, Any]:
    return await svc(request).refresh_model_candidates()


@router.get("/api/models/directory")
async def model_directory(request: Request) -> dict[str, Any]:
    return svc(request).model_directory()


@router.post("/api/models/directory/refresh")
async def refresh_model_directory(request: Request) -> dict[str, Any]:
    return await svc(request).refresh_model_directory()


@router.get("/api/sessions/{session_id}/payslip")
async def payslip(request: Request, session_id: str) -> dict[str, Any]:
    return svc(request).payslip(session_id)


@router.get("/api/contributions")
async def contributions(request: Request) -> list[dict[str, Any]]:
    return svc(request).contributions()


@router.get("/api/sessions")
async def sessions(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    return svc(request).sessions(min(max(limit, 1), 200))


@router.post("/api/sessions", status_code=202)
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


@router.get("/api/sessions/{session_id}/carry")
async def carry(request: Request, session_id: str) -> dict[str, Any]:
    """接力用：上一场的题目和将带入下一场的成果摘要。"""
    return svc(request).carry(session_id)


@router.get("/api/sessions/{session_id}/files/{file_id}")
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


@router.get("/api/export/options")
async def export_options(request: Request) -> dict[str, Any]:
    """导出面板的可选项；保存文件夹只告诉本机（平板看不到电脑上的路径）。"""
    data = svc(request).export_options()
    data["can_save_to_folder"] = not is_remote(_client(request.scope))
    if not data["can_save_to_folder"]:
        data["folder"] = data["suggested_folder"] = ""
    return data


@router.get("/api/sessions/{session_id}/export")
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


@router.post("/api/sessions/{session_id}/export/save")
async def export_save(request: Request, session_id: str, body: ExportSave) -> dict[str, Any]:
    """直接存进电脑上的文件夹。只允许在电脑本机上用，平板 / 手机不能往电脑里写文件。"""
    if is_remote(_client(request.scope)):
        raise ServiceError("只能在电脑上直接存到文件夹；平板上请用下载", 403)
    options = body.model_dump(exclude={"format", "folder"})
    return svc(request).export_save(session_id, body.format, body.folder, options)


@router.get("/api/sessions/{session_id}/files/{file_id}/preview")
async def file_preview(request: Request, session_id: str, file_id: str) -> dict[str, Any]:
    return svc(request).file_preview(session_id, file_id)


@router.post("/api/estimate")
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


@router.post("/api/uploads", status_code=201)
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


@router.get("/api/sessions/{session_id}")
async def session(request: Request, session_id: str) -> dict[str, Any]:
    return svc(request).session(session_id)


@router.post("/api/sessions/{session_id}/respond", status_code=202)
async def respond(request: Request, session_id: str, body: Respond) -> dict[str, str]:
    svc(request).respond(session_id, body.response, body.note)
    return {"session_id": session_id}


@router.post("/api/sessions/{session_id}/notes", status_code=201)
async def add_note(request: Request, session_id: str, body: Note) -> dict[str, Any]:
    return svc(request).add_note(session_id, body.text)


@router.post("/api/sessions/{session_id}/resume", status_code=202)
async def resume(request: Request, session_id: str) -> dict[str, str]:
    svc(request).resume(session_id)
    return {"session_id": session_id}


@router.post("/api/sessions/{session_id}/reveal")
async def reveal(request: Request, session_id: str) -> dict[str, Any]:
    return svc(request).reveal_identities(session_id)


@router.get("/api/sessions/{session_id}/events")
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
