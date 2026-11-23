"""游戏区（docs/ARCADE.md）：德州扑克的开桌、出牌、下一手、押注、离桌。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from roundtable.api.bodies import ArcadeActBody, ArcadeDealBody, ArcadeOpenBody, ArcadeThinkBody
from roundtable.api.common import svc

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


@router.post("/api/arcade/table/deal")
async def arcade_deal(request: Request, body: ArcadeDealBody) -> dict[str, Any]:
    return svc(request).arcade_deal(body.bet_on, body.amount)


@router.put("/api/arcade/table/think")
async def arcade_think(request: Request, body: ArcadeThinkBody) -> dict[str, Any]:
    return svc(request).arcade_think(body.on)


@router.delete("/api/arcade/table")
async def arcade_leave(request: Request) -> dict[str, Any]:
    return svc(request).arcade_leave()
