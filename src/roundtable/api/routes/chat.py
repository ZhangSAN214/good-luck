"""群聊与私聊：聊天、感官、表情包、人设、记忆、成员档案、成长、关系、状态、关于你、订阅源与梗。

从 app.py 拆出（代码精简第 2 批）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from roundtable.api.bodies import (
    AboutClassBody,
    AboutDdlBody,
    AboutDomainBody,
    AboutFactBody,
    AboutGameBody,
    AboutLockBody,
    AboutPlayerBody,
    AboutStatusBody,
    ChatBody,
    FeedsBody,
    ForwardBody,
    GrowthRevertBody,
    GrowthUndoBody,
    HandoffBody,
    MemeBody,
    MemoryBody,
    PatBody,
    PersonaBody,
    ProfileImportBody,
    ProfileLockBody,
    ProfilePatchBody,
    ProfileRevertBody,
    RecallBody,
    StickerPackBody,
    StickerReviewBody,
    ViewReplyBody,
    VoiceBody,
)
from roundtable.api.common import _byte_range, svc
from roundtable.core.service import CHAT_MEDIA_MAX, ServiceError

router = APIRouter()


@router.get("/api/members")
async def members(request: Request) -> dict[str, Any]:
    return svc(request).members()


@router.get("/api/members/files/{path:path}")
async def member_file(request: Request, path: str) -> Response:
    data, mime = svc(request).member_file(path)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
    return Response(content=data, media_type=mime, headers=headers)


@router.get("/api/chat/rooms")
async def chat_rooms(request: Request) -> dict[str, Any]:
    return svc(request).chat_rooms()


@router.get("/api/chat")
async def chat(
    request: Request, after: int = 0, q: str | None = None, room: str = "main"
) -> dict[str, Any]:
    return svc(request).chat_view(after, q, room)


@router.post("/api/chat", status_code=202)
async def chat_post(request: Request, body: ChatBody) -> dict[str, Any]:
    return await svc(request).chat_post(
        body.text, body.room, body.media, body.quote, files=body.files
    )


@router.post("/api/chat/pat", status_code=202)
async def chat_pat(request: Request, body: PatBody) -> dict[str, Any]:
    return await svc(request).chat_pat(body.member, body.room)


@router.post("/api/chat/forward", status_code=202)
async def chat_forward(request: Request, body: ForwardBody) -> dict[str, Any]:
    return await svc(request).chat_forward(body.id, body.room)


@router.post("/api/chat/recall")
async def chat_recall(request: Request, body: RecallBody) -> dict[str, Any]:
    return svc(request).chat_recall(body.id, body.room)


@router.post("/api/chat/media", status_code=201)
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


@router.get("/api/chat/media/{name}")
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


@router.post("/api/chat/messages/{message_id}/look", status_code=202)
async def chat_look(request: Request, message_id: int, room: str = "main") -> dict[str, Any]:
    """「细看」：按精细模式重新整理这条消息里的图片 / 视频 / 语音，再请一位成员回应。"""
    return await svc(request).chat_look(room, message_id)


@router.delete("/api/perception")
async def perception_clear(request: Request) -> dict[str, Any]:
    """一键清空感官缓存（本机 data/perception/）。"""
    return svc(request).perception_clear()


@router.post("/api/chat/handoff", status_code=202)
async def chat_handoff(request: Request, body: HandoffBody) -> dict[str, Any]:
    return svc(request).chat_handoff(body.text)


@router.post("/api/chat/handoff/{message_id}/start", status_code=202)
async def chat_handoff_start(
    request: Request, message_id: int, room: str = "main"
) -> dict[str, Any]:
    """聊天里成员交办的事：你点了「交给办公室」才开工（docs/CHAT.md G14）。"""
    return svc(request).chat_handoff_start(room, message_id)


@router.post("/api/chat/handoff/{message_id}/dismiss")
async def chat_handoff_dismiss(
    request: Request, message_id: int, room: str = "main"
) -> dict[str, Any]:
    return svc(request).chat_handoff_dismiss(room, message_id)


@router.get("/api/chat/voices")
async def chat_voices(request: Request) -> dict[str, Any]:
    return svc(request).member_voices()


@router.post("/api/chat/voices/{member}")
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


@router.get("/api/stickers")
async def stickers(request: Request) -> dict[str, Any]:
    return svc(request).member_stickers()


@router.delete("/api/stickers/stash")
async def sticker_stash_clear(request: Request, member: str | None = None) -> dict[str, Any]:
    """清空成员从你那收来的表情包（member 省略时所有人）。"""
    return svc(request).stash_clear(member)


@router.delete("/api/stickers/stash/{member}/{n}")
async def sticker_stash_delete(request: Request, member: str, n: int) -> dict[str, Any]:
    return svc(request).stash_delete(member, n)


@router.post("/api/stickers/{member}/{name}/draw")
async def sticker_draw(request: Request, member: str, name: str) -> dict[str, Any]:
    """画（或重画）一张专属表情；新画的是「待审」。花费计入表情包与创作输出的月上限。"""
    return await svc(request).draw_sticker(member, name)


@router.post("/api/stickers/{member}/{name}/review")
async def sticker_review(
    request: Request, member: str, name: str, body: StickerReviewBody
) -> dict[str, Any]:
    return svc(request).review_sticker(member, name, body.status)


@router.get("/api/stickers/{member}/{name}/image")
async def sticker_image(request: Request, member: str, name: str) -> Response:
    data = svc(request).sticker_file(member, name)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=86400"}
    mime = "image/gif" if data[:3] == b"GIF" else "image/png"
    return Response(content=data, media_type=mime, headers=headers)


@router.get("/api/emoji")
async def emoji_status(request: Request) -> dict[str, Any]:
    """网页里的 emoji 显示成哪套图。"""
    return svc(request).emoji_status()


@router.get("/api/emoji/{code}.png")
def emoji_image(request: Request, code: str) -> Response:
    """一个 emoji 的图（用到才下载到本机）；没有时 404，网页退回系统字体。"""
    data = svc(request).emoji_image(code)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=2592000"}
    return Response(content=data, media_type="image/png", headers=headers)


@router.get("/api/sticker-packs")
async def sticker_packs(request: Request) -> dict[str, Any]:
    """本机装的外部表情包（dsh-meme 格式）。"""
    return svc(request).sticker_packs()


@router.get("/api/sticker-packs/catalog")
def sticker_pack_catalog(request: Request) -> dict[str, Any]:
    """在线目录里的包（要联网）。"""
    return svc(request).sticker_pack_catalog()


@router.post("/api/sticker-packs")
def sticker_pack_install(request: Request, body: StickerPackBody) -> dict[str, Any]:
    """从在线目录装一个包（下载、核对 sha256）；owner 为空 = 通用。"""
    return svc(request).install_sticker_pack(body.id, body.owner, body.label or "")


@router.patch("/api/sticker-packs/{pack_id}")
async def sticker_pack_assign(
    request: Request, pack_id: str, body: StickerPackBody
) -> dict[str, Any]:
    return svc(request).assign_sticker_pack(pack_id, body.owner, body.label)


@router.delete("/api/sticker-packs/{pack_id}")
async def sticker_pack_remove(request: Request, pack_id: str) -> dict[str, Any]:
    return svc(request).remove_sticker_pack(pack_id)


@router.get("/api/sticker-packs/{pack_id}/{n}")
async def sticker_pack_image(request: Request, pack_id: str, n: int) -> Response:
    data, mime = svc(request).sticker_pack_image(pack_id, n)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=2592000"}
    return Response(content=data, media_type=mime, headers=headers)


@router.get("/api/chat/stickers")
async def chat_stickers(request: Request) -> dict[str, Any]:
    """聊天里显示的成员专属表情（审过的那些）。"""
    return svc(request).chat_sticker_packs()


@router.get("/api/chat/personas")
async def chat_personas(request: Request) -> dict[str, Any]:
    return svc(request).chat_personas()


@router.put("/api/chat/personas/{member}")
async def chat_set_persona(request: Request, member: str, body: PersonaBody) -> dict[str, Any]:
    return svc(request).chat_set_persona(member, body.persona)


@router.get("/api/chat/memories")
async def chat_memories(request: Request, member: str | None = None) -> dict[str, Any]:
    return svc(request).chat_memories(member)


@router.post("/api/chat/memories", status_code=201)
async def chat_add_memory(request: Request, body: MemoryBody) -> dict[str, Any]:
    if not body.member:
        raise ServiceError("要写明是哪位成员的记忆")
    return svc(request).chat_add_memory(body.member, body.text)


@router.post("/api/chat/memories/tidy")
async def chat_tidy_memories(request: Request) -> dict[str, Any]:
    return await svc(request).chat_tidy_memories()


@router.patch("/api/chat/memories/{memory_id}")
async def chat_update_memory(request: Request, memory_id: int, body: MemoryBody) -> dict[str, Any]:
    return svc(request).chat_update_memory(memory_id, body.text)


@router.delete("/api/chat/memories/{memory_id}")
async def chat_delete_memory(request: Request, memory_id: int) -> dict[str, Any]:
    return svc(request).chat_delete_memory(memory_id)


@router.delete("/api/chat/memories")
async def chat_clear_memories(request: Request, member: str | None = None) -> dict[str, Any]:
    return svc(request).chat_clear_memories(member)


@router.get("/api/chat/memory/{member}/items")
async def chat_memory_items(
    request: Request, member: str, q: str | None = None, limit: int = 50
) -> dict[str, Any]:
    return svc(request).chat_memory_items(member, q, limit)


@router.get("/api/chat/memory/{member}/diary")
async def chat_read_diary(request: Request, member: str, q: str = "") -> dict[str, Any]:
    return svc(request).chat_read_diary(member, q)


@router.get("/api/chat/memory/items/{item_id}/history")
async def chat_memory_item_history(request: Request, item_id: int) -> dict[str, Any]:
    return svc(request).chat_memory_item_history(item_id)


@router.patch("/api/chat/memory/items/{item_id}")
async def chat_update_memory_item(
    request: Request, item_id: int, body: MemoryBody
) -> dict[str, Any]:
    return svc(request).chat_update_memory_item(item_id, body.text)


@router.delete("/api/chat/memory/items/{item_id}")
async def chat_delete_memory_item(request: Request, item_id: int) -> dict[str, Any]:
    return svc(request).chat_delete_memory_item(item_id)


@router.delete("/api/chat/memory/hanging/{hanging_id}")
async def chat_drop_hanging(request: Request, hanging_id: int) -> dict[str, Any]:
    return svc(request).chat_drop_hanging(hanging_id)


# 成员引擎：自我档案（docs/MEMBER_ENGINE.md）
@router.get("/api/chat/profiles")
async def member_profiles(request: Request) -> dict[str, Any]:
    return svc(request).member_profiles()


@router.post("/api/chat/profiles/import")
async def member_profile_import(request: Request, body: ProfileImportBody) -> dict[str, Any]:
    return svc(request).member_profile_import(body.text, body.mapping, body.dry_run)


@router.get("/api/chat/profiles/{member}")
async def member_profile(request: Request, member: str) -> dict[str, Any]:
    return svc(request).member_profile(member)


@router.patch("/api/chat/profiles/{member}")
async def member_profile_change(
    request: Request, member: str, body: ProfilePatchBody
) -> dict[str, Any]:
    return svc(request).member_profile_change(member, body.patch, body.reason)


@router.post("/api/chat/profiles/{member}/lock")
async def member_profile_lock(
    request: Request, member: str, body: ProfileLockBody
) -> dict[str, Any]:
    return svc(request).member_profile_lock(member, body.path, body.locked)


@router.post("/api/chat/profiles/{member}/revert")
async def member_profile_revert(
    request: Request, member: str, body: ProfileRevertBody
) -> dict[str, Any]:
    return svc(request).member_profile_revert(member, body.version)


@router.delete("/api/chat/profiles/{member}")
async def member_profile_clear(request: Request, member: str) -> dict[str, Any]:
    return svc(request).member_profile_clear(member)


# 成员成长（P6）：时间线、马上反思上一周、撤回一条、清空时间线
@router.get("/api/chat/growth/{member}")
async def member_growth(request: Request, member: str) -> dict[str, Any]:
    return svc(request).member_growth(member)


@router.post("/api/chat/growth/{member}/run")
async def member_growth_run(request: Request, member: str) -> dict[str, Any]:
    return await svc(request).member_growth_run(member)


@router.post("/api/chat/growth/{member}/revert")
async def member_growth_revert(
    request: Request, member: str, body: GrowthRevertBody
) -> dict[str, Any]:
    return svc(request).member_growth_revert(member, body.id)


@router.post("/api/chat/growth/{member}/undo")
async def member_growth_undo(request: Request, member: str, body: GrowthUndoBody) -> dict[str, Any]:
    return svc(request).member_growth_undo(member, body.token)


@router.delete("/api/chat/growth/{member}")
async def member_growth_clear(request: Request, member: str) -> dict[str, Any]:
    return svc(request).member_growth_clear(member)


# 成员关系（P4）：和你的阶段、同事之间的亲近度与看法；清空
@router.get("/api/chat/relation/{member}")
async def member_relation(request: Request, member: str) -> dict[str, Any]:
    return svc(request).member_relation(member)


@router.post("/api/chat/relation/{member}/insight")
async def member_relation_insight(request: Request, member: str) -> dict[str, Any]:
    return await svc(request).member_relation_insight(member)


@router.delete("/api/chat/relation/{member}/views/{index}")
async def member_relation_drop_view(request: Request, member: str, index: int) -> dict[str, Any]:
    return svc(request).member_relation_drop_view(member, index)


@router.put("/api/chat/relation/{member}/views/{index}/reply")
async def member_relation_reply_view(
    request: Request, member: str, index: int, body: ViewReplyBody
) -> dict[str, Any]:
    return svc(request).member_relation_reply_view(member, index, body.text)


@router.delete("/api/chat/relation")
async def member_relation_clear(request: Request, member: str | None = None) -> dict[str, Any]:
    return svc(request).member_relation_clear(member)


@router.get("/api/chat/engine/state")
async def member_states(request: Request) -> dict[str, Any]:
    return svc(request).member_states()


@router.delete("/api/chat/engine/state")
async def member_states_clear(request: Request, member: str | None = None) -> dict[str, Any]:
    return svc(request).member_states_clear(member)


# 关于你（成员系统 P8）：8 位共用的用户画像
@router.get("/api/chat/about")
async def about_view(request: Request) -> dict[str, Any]:
    return svc(request).about_view()


@router.post("/api/chat/about/facts", status_code=201)
async def about_add(request: Request, body: AboutFactBody) -> dict[str, Any]:
    return svc(request).about_add(body.text or "", body.domain)


@router.patch("/api/chat/about/facts/{fact_id}")
async def about_edit(request: Request, fact_id: int, body: AboutFactBody) -> dict[str, Any]:
    return svc(request).about_edit(fact_id, body.text, body.domain)


@router.delete("/api/chat/about/facts/{fact_id}")
async def about_delete(request: Request, fact_id: int) -> dict[str, Any]:
    return svc(request).about_delete(fact_id)


@router.post("/api/chat/about/facts/{fact_id}/reject")
async def about_reject(request: Request, fact_id: int) -> dict[str, Any]:
    return svc(request).about_reject(fact_id)


@router.post("/api/chat/about/facts/{fact_id}/lock")
async def about_lock(request: Request, fact_id: int, body: AboutLockBody) -> dict[str, Any]:
    return svc(request).about_lock(fact_id, body.locked)


@router.delete("/api/chat/about/rejected/{index}")
async def about_forget_rejected(request: Request, index: int) -> dict[str, Any]:
    return svc(request).about_forget_rejected(index)


@router.post("/api/chat/about/domains")
async def about_domain(request: Request, body: AboutDomainBody) -> dict[str, Any]:
    return svc(request).about_domain(body.domain, body.on)


@router.delete("/api/chat/about")
async def about_clear(request: Request, rejected: bool = False) -> dict[str, Any]:
    return svc(request).about_clear(rejected)


@router.get("/api/chat/about/export")
async def about_export(request: Request, fmt: str = "json") -> Response:
    data, media, name = svc(request).about_export(fmt)
    return Response(
        data,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# 关于你 · 你的状态（P8 第二块；每项默认关）
@router.get("/api/chat/about/status")
async def about_status(request: Request) -> dict[str, Any]:
    return svc(request).about_status()


@router.post("/api/chat/about/status")
async def about_status_set(request: Request, body: AboutStatusBody) -> dict[str, Any]:
    return svc(request).about_status_set(body.kind, body.on)


@router.post("/api/chat/about/status/player")
async def about_music_player(request: Request, body: AboutPlayerBody) -> dict[str, Any]:
    return svc(request).about_music_player(body.player)


@router.post("/api/chat/about/games")
async def about_game_add(request: Request, body: AboutGameBody) -> dict[str, Any]:
    return svc(request).about_game_add(body.exe, body.name)


@router.delete("/api/chat/about/games")
async def about_game_del(request: Request, exe: str) -> dict[str, Any]:
    return svc(request).about_game_del(exe)


@router.post("/api/chat/about/schedule/classes")
async def about_class_add(request: Request, body: AboutClassBody) -> dict[str, Any]:
    return svc(request).about_class_add(body.weekday, body.start, body.end, body.name)


@router.post("/api/chat/about/schedule/ddls")
async def about_ddl_add(request: Request, body: AboutDdlBody) -> dict[str, Any]:
    return svc(request).about_ddl_add(body.name, body.due)


@router.delete("/api/chat/about/schedule/{kind}/{item_id}")
async def about_schedule_del(request: Request, kind: str, item_id: int) -> dict[str, Any]:
    return svc(request).about_schedule_del(kind, item_id)


@router.delete("/api/chat/about/schedule")
async def about_schedule_clear(request: Request) -> dict[str, Any]:
    return svc(request).about_schedule_clear()


@router.get("/api/chat/engine/events")
async def member_events_count(request: Request) -> dict[str, Any]:
    return svc(request).member_events_count()


@router.delete("/api/chat/engine/events")
async def member_events_clear(request: Request) -> dict[str, Any]:
    return svc(request).member_events_clear()


@router.get("/api/chat/feeds")
async def chat_feeds(request: Request) -> dict[str, Any]:
    return svc(request).chat_feeds()


@router.put("/api/chat/feeds")
async def chat_set_feeds(request: Request, body: FeedsBody) -> dict[str, Any]:
    return svc(request).chat_set_feeds(body.feeds)


@router.get("/api/chat/memes")
async def chat_memes(request: Request) -> dict[str, Any]:
    return svc(request).chat_memes()


@router.post("/api/chat/memes")
async def chat_add_meme(request: Request, body: MemeBody) -> dict[str, Any]:
    return svc(request).chat_add_meme(body.model_dump())


@router.delete("/api/chat/memes")
async def chat_remove_meme(request: Request, phrase: str) -> dict[str, Any]:
    return svc(request).chat_remove_meme(phrase)


@router.post("/api/chat/memes/refresh")
async def chat_refresh_memes(request: Request) -> dict[str, Any]:
    return await svc(request).chat_refresh_memes()


@router.post("/api/chat/feeds/refresh")
async def chat_refresh_feeds(request: Request) -> dict[str, Any]:
    return await svc(request).chat_refresh_feeds()


@router.delete("/api/chat")
async def chat_clear(request: Request, room: str = "main") -> dict[str, Any]:
    return svc(request).chat_clear(room)
