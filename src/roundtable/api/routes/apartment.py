"""公寓楼：旅行与 B 站、成员自己上网、朋友圈、星图、生活报告、公寓总览。

从 app.py 拆出（代码精简第 2 批）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import Response

from roundtable.api.bodies import (
    BiliWatchBody,
    MomentBody,
    MomentCommentBody,
    TripSwitchBody,
    WanderStartBody,
)
from roundtable.api.common import svc

router = APIRouter()


@router.get("/api/trips")
async def trips(request: Request) -> dict[str, Any]:
    return svc(request).trips()


@router.post("/api/trips/switches")
async def trip_switches(request: Request, body: TripSwitchBody) -> dict[str, Any]:
    return svc(request).set_trip_switches(auto=body.auto, photo=body.photo)


@router.post("/api/trips/bili/watch")
async def bili_watch(request: Request, body: BiliWatchBody) -> dict[str, Any]:
    return await svc(request).bili_watch(body.member, body.url)


@router.get("/api/wander")
async def wander_status(request: Request) -> dict[str, Any]:
    return svc(request).wander_status()


@router.post("/api/wander/start")
async def wander_start(request: Request, body: WanderStartBody) -> dict[str, Any]:
    return svc(request).wander_start(body.member, body.site)


@router.post("/api/wander/stop")
async def wander_stop(request: Request) -> dict[str, Any]:
    return svc(request).wander_stop()


@router.get("/api/wander/log")
async def wander_log(request: Request, member: str, after: int = 0) -> dict[str, Any]:
    return {"messages": svc(request).wander_log(member, after)}


@router.get("/api/wander/frame")
async def wander_frame(request: Request) -> Response:
    frame = svc(request).wander_frame()
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"}
    if frame is None:
        return Response(status_code=204, headers=headers)
    return Response(content=frame, media_type="image/jpeg", headers=headers)


@router.get("/api/moments")
async def moments(
    request: Request, author: str | None = None, before: int | None = None
) -> dict[str, Any]:
    return svc(request).moments_view(author, before)


@router.post("/api/moments", status_code=201)
async def moments_post(request: Request, body: MomentBody) -> dict[str, Any]:
    return await svc(request).moments_post(body.text, body.media)


@router.post("/api/moments/{moment_id}/like")
async def moments_like(request: Request, moment_id: int) -> dict[str, Any]:
    return svc(request).moments_like(moment_id)


@router.post("/api/moments/{moment_id}/comments", status_code=201)
async def moments_comment(
    request: Request, moment_id: int, body: MomentCommentBody
) -> dict[str, Any]:
    return await svc(request).moments_comment(moment_id, body.text, body.reply_to)


@router.delete("/api/moments/{moment_id}")
async def moments_delete(request: Request, moment_id: int) -> dict[str, Any]:
    return svc(request).moments_delete(moment_id)


@router.delete("/api/moments/comments/{comment_id}")
async def moments_delete_comment(request: Request, comment_id: int) -> dict[str, Any]:
    return svc(request).moments_delete_comment(comment_id)


# 星图（docs/STARMAP.md）：圆桌星图、成员的人生星图（只读）
@router.get("/api/starmap")
async def star_map(request: Request) -> dict[str, Any]:
    return svc(request).star_map()


@router.get("/api/starmap/{member}")
async def star_map_member(request: Request, member: str) -> dict[str, Any]:
    return svc(request).star_map_member(member)


@router.get("/api/life/reports")
async def life_reports(request: Request) -> dict[str, Any]:
    return svc(request).life_reports()


@router.get("/api/apartment")
async def apartment(request: Request, hour: int, weekday: int) -> dict[str, Any]:
    return svc(request).apartment(hour, weekday)
