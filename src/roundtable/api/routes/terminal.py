"""终端首页：大立绘、正在放的歌、一起看（投屏）、背景音乐、公告、主时区、日历、邮件栏、游戏模式。

从 app.py 拆出（代码精简第 2 批）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import Response

from roundtable.api.bodies import (
    AdbConnectBody,
    AdbPairBody,
    CalendarDateBody,
    GameModeBody,
    HomeTzBody,
    MailAIBody,
    MailCategoryBody,
    MailIdBody,
    MailMarkBody,
    MailRefreshBody,
    MailReminderBody,
    MailSettingsBody,
    NowPlayingControlBody,
    WatchQuietBody,
    WatchSayBody,
    WatchScrcpyBody,
    WatchStartBody,
)
from roundtable.api.common import svc, watch_local

router = APIRouter()


@router.get("/api/standee")
async def standee(request: Request) -> dict[str, Any]:
    return svc(request).standee()


@router.get("/api/standee/files/{path:path}")
async def standee_file(request: Request, path: str) -> Response:
    data, mime = svc(request).standee_file(path)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
    return Response(content=data, media_type=mime, headers=headers)


@router.get("/api/nowplaying")
async def nowplaying(request: Request, player: str = "qqmusic") -> dict[str, Any]:
    return await asyncio.to_thread(svc(request).nowplaying, player)


@router.get("/api/nowplaying/cover")
async def nowplaying_cover(request: Request, player: str = "qqmusic") -> Response:
    data, mime = await asyncio.to_thread(svc(request).nowplaying_cover, player)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=600"}
    return Response(content=data, media_type=mime, headers=headers)


@router.post("/api/nowplaying/control")
async def nowplaying_control(request: Request, body: NowPlayingControlBody) -> dict[str, Any]:
    return await asyncio.to_thread(
        svc(request).nowplaying_control, body.player, body.action, body.position
    )


@router.get("/api/watch")
async def watch_status(request: Request, devices: bool = False) -> dict[str, Any]:
    if devices:
        watch_local(request)
        return await asyncio.to_thread(svc(request).watch_status, True)
    return svc(request).watch_status()


@router.post("/api/watch/start")
async def watch_start(request: Request, body: WatchStartBody) -> dict[str, Any]:
    watch_local(request)
    return await svc(request).watch_start(body.source, body.watchers, body.consent)


@router.post("/api/watch/stop")
async def watch_stop(request: Request) -> dict[str, Any]:
    return svc(request).watch_stop()


@router.post("/api/watch/quiet")
async def watch_quiet(request: Request, body: WatchQuietBody) -> dict[str, Any]:
    return svc(request).watch_quiet(body.quiet)


@router.get("/api/watch/frame")
async def watch_frame(request: Request) -> Response:
    watch_local(request)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    return Response(content=svc(request).watch_frame(), media_type="image/jpeg", headers=headers)


@router.get("/api/watch/messages")
async def watch_messages(request: Request, after: int = 0) -> dict[str, Any]:
    return svc(request).watch_messages(after)


@router.post("/api/watch/say", status_code=202)
async def watch_say(request: Request, body: WatchSayBody) -> dict[str, Any]:
    return await svc(request).watch_say(body.text)


@router.post("/api/watch/adb/pair")
async def watch_adb_pair(request: Request, body: AdbPairBody) -> dict[str, Any]:
    watch_local(request)
    return await asyncio.to_thread(svc(request).watch_adb_pair, body.host, body.port, body.code)


@router.post("/api/watch/adb/connect")
async def watch_adb_connect(request: Request, body: AdbConnectBody) -> dict[str, Any]:
    watch_local(request)
    return await asyncio.to_thread(svc(request).watch_adb_connect, body.host, body.port)


@router.post("/api/watch/overlay")
async def watch_overlay(request: Request) -> dict[str, Any]:
    watch_local(request)
    port = request.url.port or (443 if request.url.scheme == "https" else 80)
    return svc(request).watch_overlay(port)


@router.post("/api/watch/adb/qr")
async def watch_adb_qr(request: Request) -> dict[str, Any]:
    watch_local(request)
    return await asyncio.to_thread(svc(request).watch_adb_qr)


@router.get("/api/watch/adb/qr")
async def watch_adb_qr_poll(request: Request) -> dict[str, Any]:
    watch_local(request)
    return await asyncio.to_thread(svc(request).watch_adb_qr_poll)


@router.post("/api/watch/scrcpy")
async def watch_scrcpy(request: Request, body: WatchScrcpyBody) -> dict[str, Any]:
    watch_local(request)
    return await asyncio.to_thread(svc(request).watch_scrcpy, body.source)


@router.get("/api/music/covers")
async def music_covers(request: Request) -> dict[str, Any]:
    return svc(request).music_covers()


@router.get("/api/music/covers/{name}")
async def music_cover(request: Request, name: str) -> Response:
    data, mime = svc(request).music_cover(name)
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
    return Response(content=data, media_type=mime, headers=headers)


@router.get("/api/changelog")
async def changelog(request: Request) -> dict[str, Any]:
    return svc(request).changelog()


@router.get("/api/gamemode")
async def gamemode(request: Request) -> dict[str, Any]:
    return svc(request).gamemode()


@router.put("/api/gamemode")
async def set_gamemode(request: Request, body: GameModeBody) -> dict[str, Any]:
    return svc(request).set_gamemode(body.manual)


@router.get("/api/home-tz")
async def home_tz(request: Request) -> dict[str, Any]:
    return svc(request).home_tz()


@router.put("/api/home-tz")
async def set_home_tz(request: Request, body: HomeTzBody) -> dict[str, Any]:
    return svc(request).set_home_tz(body.zone)


@router.get("/api/calendar")
async def calendar(
    request: Request, days: int | None = None, start: str | None = None
) -> dict[str, Any]:
    return svc(request).calendar_view(days, start)


@router.post("/api/calendar/dates")
async def calendar_add_date(request: Request, body: CalendarDateBody) -> dict[str, Any]:
    return svc(request).calendar_add_date(body.name, body.date, body.lunar)


@router.delete("/api/calendar/dates/{date_id}")
async def calendar_remove_date(request: Request, date_id: str) -> dict[str, Any]:
    return svc(request).calendar_remove_date(date_id)


@router.get("/api/mail")
async def mail(request: Request) -> dict[str, Any]:
    return svc(request).mail_view()


@router.put("/api/mail/settings")
async def mail_settings(request: Request, body: MailSettingsBody) -> dict[str, Any]:
    return svc(request).mail_settings(**body.model_dump())


@router.post("/api/mail/login")
async def mail_login(request: Request) -> dict[str, Any]:
    return svc(request).mail_login()


@router.post("/api/mail/refresh")
async def mail_refresh(request: Request, body: MailRefreshBody) -> dict[str, Any]:
    return svc(request).mail_refresh(manual=body.manual)


@router.get("/api/mail/messages/{message_id:path}")
async def mail_message(request: Request, message_id: str) -> dict[str, Any]:
    return svc(request).mail_message(message_id)


@router.post("/api/mail/reminders/{reminder_id}")
async def mail_reminder(
    request: Request, reminder_id: int, body: MailReminderBody
) -> dict[str, Any]:
    return svc(request).mail_reminder(reminder_id, body.action)


@router.post("/api/mail/mark")
async def mail_mark(request: Request, body: MailMarkBody) -> dict[str, Any]:
    return svc(request).mail_mark(body.id, body.mark)


@router.post("/api/mail/category")
async def mail_category(request: Request, body: MailCategoryBody) -> dict[str, Any]:
    return svc(request).mail_category(body.id, body.category)


@router.post("/api/mail/images")
async def mail_images(request: Request, body: MailIdBody) -> dict[str, Any]:
    return svc(request).mail_images(body.id)


@router.post("/api/mail/ai/{kind}")
async def mail_ai(
    request: Request, kind: Literal["summary", "translate", "digest"], body: MailAIBody
) -> dict[str, Any]:
    return await svc(request).mail_ai_do(kind, body.id, again=body.again)


@router.post("/api/mail/logout")
async def mail_logout(request: Request) -> dict[str, Any]:
    return svc(request).mail_logout()


@router.delete("/api/mail/cache")
async def mail_clear(request: Request) -> dict[str, Any]:
    return svc(request).mail_clear()
