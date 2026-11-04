"""系统：状态、预算与用量、渠道顺序、出图渠道、局域网访问、备份、自检、配对。

从 app.py 拆出（代码精简第 2 批）。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from roundtable.api.bodies import (
    BackupPoint,
    BackupRestore,
    ChannelPriorityBody,
    DrawChannelsBody,
    LanPair,
    LanPassword,
)
from roundtable.api.common import _client, _lan_cookie, local_only, svc
from roundtable.core.service import LAN_COOKIE, ServiceError, qr_svg

router = APIRouter()


@router.get("/api/status")
async def status(request: Request) -> dict[str, Any]:
    return svc(request).status()


@router.get("/api/budget")
async def budget(request: Request) -> dict[str, Any]:
    return svc(request).budget()


@router.get("/api/usage")
async def usage(request: Request, range: str = "month") -> dict[str, Any]:  # noqa: A002
    return svc(request).usage(range)


@router.get("/api/usage/balance")
async def usage_balance(request: Request, refresh: int = 0) -> dict[str, Any]:
    return await svc(request).account_balance(refresh=bool(refresh))


@router.get("/api/channels/priority")
async def channel_priority(request: Request) -> dict[str, Any]:
    return svc(request).channel_priority()


@router.put("/api/channels/priority")
async def set_channel_priority(request: Request, body: ChannelPriorityBody) -> dict[str, Any]:
    return svc(request).set_channel_priority(
        strategy=body.strategy, order=body.order, credits=body.credits
    )


@router.get("/api/media/draw-channels")
async def draw_channels(request: Request) -> dict[str, Any]:
    return svc(request).draw_channels()


@router.put("/api/media/draw-channels")
async def set_draw_channels(request: Request, body: DrawChannelsBody) -> dict[str, Any]:
    return svc(request).set_draw_channels(disabled=body.disabled, order=body.order)


@router.get("/api/lan")
async def lan_status(request: Request) -> dict[str, Any]:
    lan = request.app.state.lan
    local_only(request)
    port = (request.scope.get("server") or ("", 8000))[1] or 8000
    return lan.status(int(port))


@router.post("/api/lan/password")
async def lan_password(request: Request, body: LanPassword) -> dict[str, Any]:
    lan = request.app.state.lan
    local_only(request)
    try:
        lan.set_password(body.password)
    except ValueError as exc:
        raise ServiceError(str(exc)) from None
    return {"ok": True}


@router.post("/api/lan/signout")
async def lan_signout(request: Request) -> dict[str, Any]:
    lan = request.app.state.lan
    local_only(request)
    lan.sign_out_all()
    return {"ok": True}


@router.post("/api/lan/pair")
async def lan_pair(request: Request, body: LanPair) -> dict[str, Any]:
    lan = request.app.state.lan
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


@router.post("/api/lan/login")
async def lan_login(request: Request, body: LanPassword) -> Response:
    lan = request.app.state.lan
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


@router.post("/api/lan/logout")
async def lan_logout() -> Response:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(LAN_COOKIE, path="/")
    return resp


@router.get("/api/backup")
async def backup_status(request: Request) -> dict[str, Any]:
    local_only(request)
    return svc(request).backup_status()


@router.post("/api/backup/checkpoint")
async def backup_checkpoint(request: Request, body: BackupPoint) -> dict[str, Any]:
    local_only(request)
    return svc(request).backup_checkpoint(body.label)  # 同一连接，不放到别的线程


@router.post("/api/backup/restore")
async def backup_restore(request: Request, body: BackupRestore) -> dict[str, Any]:
    local_only(request)
    return svc(request).backup_restore(body.name)


@router.get("/api/firstaid")
async def firstaid(request: Request) -> dict[str, Any]:
    local_only(request)
    return svc(request).firstaid()


@router.get("/api/firstaid/report")
async def firstaid_report(request: Request) -> dict[str, Any]:
    local_only(request)
    return svc(request).firstaid_report()


@router.get("/lan/pair")
async def lan_pair_open(request: Request, c: str = "") -> Response:
    lan = request.app.state.lan
    token = lan.redeem_pair_code(c)
    if token is None:
        return RedirectResponse("/login.html?expired=1", status_code=303)
    resp = RedirectResponse("/", status_code=303)
    _lan_cookie(resp, token)
    return resp
