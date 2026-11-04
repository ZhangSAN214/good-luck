"""接口层公用的小工具：取服务、只限电脑本机、区间下载、SSE、静态文件、局域网门卫。

从 app.py 拆出（代码精简第 2 批）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from roundtable.core.service import (
    LAN_COOKIE,
    LanAccess,
    RoundtableService,
    ServiceError,
    is_local,
    is_remote,
)

WEB_DIR = Path(__file__).resolve().parents[3] / "web"


# 可以在页面里直接显示的图片类型（SVG 不在其中：只作为文本预览或下载）
INLINE_IMAGES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})


# 生成的音频 / 视频可以在页面里播放（支持 Range，便于拖动进度条）
INLINE_MEDIA = frozenset({"audio/mpeg", "audio/wav", "audio/ogg", "video/mp4", "video/webm"})


# 生成的工作台在「沙箱」里运行：浏览器把它当成一个陌生网站，碰不到圆桌的接口、存储和登录状态
# 能联网取的只有配置里那几项公开数据（地图底图、天气、汇率……），其余网址一律拦下
def bench_csp(origins: list[str]) -> str:
    extra = "".join(f" {o}" for o in origins)
    return (
        "sandbox allow-scripts allow-forms allow-popups allow-downloads; "
        "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; "
        f"style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:{extra}; "
        "media-src 'self' data: blob:; "
        f"connect-src 'self'{extra}; frame-ancestors 'self'"
    )


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
        if "/kit/" in str(full_path).replace("\\", "/"):
            # 设计套件：沙箱里的工作台（来源是「陌生网站」）也要能用 import 载入
            resp.headers["Access-Control-Allow-Origin"] = "*"
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


def svc(request: Request) -> RoundtableService:
    return request.app.state.service


def watch_local(request: Request) -> None:
    if is_remote(_client(request.scope)):
        raise ServiceError("投屏只能在电脑上开始和预览；平板上可以聊天和停止", 403)


# dsh 工作台：网页地址带一次性口令，只给电脑本机（dsh 只监听 127.0.0.1，平板也打不开）
def dsh_out(request: Request, out: dict[str, Any]) -> dict[str, Any]:
    if out.get("url") and not is_local(_client(request.scope)):
        out = {**out, "url": None, "local_only": True}
    return out


def local_only(request: Request) -> None:
    if is_remote(_client(request.scope)):
        raise ServiceError("只能在电脑上设置", 403)
