"""游戏区（docs/ARCADE.md）：开桌、出牌（德州 / 斗地主 / 跑得快）、下一手、押注、离桌。"""

from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import APIRouter, Request, Response

from roundtable.api.bodies import (
    ArcadeActBody,
    ArcadeDealBody,
    ArcadeOpenBody,
    ArcadePlayBody,
    ArcadeTanksResultBody,
    ArcadeTanksStartBody,
    ArcadeThinkBody,
    ArcadeWorkshopBody,
)
from roundtable.api.common import bench_csp, svc

router = APIRouter()


@router.get("/api/arcade")
async def arcade(request: Request) -> dict[str, Any]:
    return svc(request).arcade_status()


@router.get("/api/arcade/table")
async def arcade_table(request: Request) -> dict[str, Any]:
    return svc(request).arcade_table()


@router.post("/api/arcade/table")
async def arcade_open(request: Request, body: ArcadeOpenBody) -> dict[str, Any]:
    return svc(request).arcade_open(body.game, body.members, sit=body.sit, think=body.think)


@router.post("/api/arcade/table/act")
async def arcade_act(request: Request, body: ArcadeActBody) -> dict[str, Any]:
    return svc(request).arcade_act(body.kind, body.to)


@router.post("/api/arcade/table/play")
async def arcade_play(request: Request, body: ArcadePlayBody) -> dict[str, Any]:
    return svc(request).arcade_play(body.kind, body.cards, body.bid)


@router.post("/api/arcade/table/deal")
async def arcade_deal(request: Request, body: ArcadeDealBody) -> dict[str, Any]:
    return svc(request).arcade_deal(body.bet_on, body.amount)


@router.put("/api/arcade/table/god")
async def arcade_god(request: Request, body: ArcadeThinkBody) -> dict[str, Any]:
    """旁观的上帝视角：看所有人的手牌。"""
    return svc(request).arcade_god(body.on)


@router.put("/api/arcade/table/think")
async def arcade_think(request: Request, body: ArcadeThinkBody) -> dict[str, Any]:
    return svc(request).arcade_think(body.on)


@router.post("/api/arcade/tanks")
async def arcade_tanks_start(request: Request, body: ArcadeTanksStartBody) -> dict[str, Any]:
    return svc(request).arcade_tanks_start(body.members)


@router.post("/api/arcade/tanks/result")
async def arcade_tanks_result(request: Request, body: ArcadeTanksResultBody) -> dict[str, Any]:
    return svc(request).arcade_tanks_result(body.token, body.place, body.kills)


@router.delete("/api/arcade/table")
async def arcade_leave(request: Request) -> dict[str, Any]:
    return svc(request).arcade_leave()


# --- 游戏工坊 -----------------------------------------------------------------


@router.get("/api/arcade/workshop")
async def workshop(request: Request) -> dict[str, Any]:
    return svc(request).workshop_status()


@router.post("/api/arcade/workshop")
async def workshop_submit(request: Request, body: ArcadeWorkshopBody) -> dict[str, Any]:
    return svc(request).workshop_submit(body.text)


@router.post("/api/arcade/workshop/cancel")
async def workshop_cancel(request: Request) -> dict[str, Any]:
    return await svc(request).workshop_cancel()


@router.delete("/api/arcade/workshop/games/{gid}")
async def workshop_delete(request: Request, gid: str) -> dict[str, Any]:
    return svc(request).workshop_delete(gid)


@router.get("/arcade-games/{gid}/{path:path}")
async def workshop_file(request: Request, gid: str, path: str) -> Response:
    """做好的游戏：和生成的工作台一样在沙箱里打开，碰不到圆桌的接口、存储和登录状态，也不能联网。"""
    full = svc(request).workshop_file(gid, path)
    mime = mimetypes.guess_type(full.name)[0] or "application/octet-stream"
    if full.suffix in (".js", ".mjs"):
        mime = "text/javascript"
    headers = {"Cache-Control": "no-cache", "Content-Security-Policy": bench_csp([])}
    return Response(content=full.read_bytes(), media_type=mime, headers=headers)
