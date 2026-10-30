"""联网自检（docs/KIT.md）：逐个试一下成品要用的公开数据（地图底图、天气、汇率、地点简介……）
和调研要用的接口（GitHub、arXiv、Semantic Scholar、dsh 插件集市），显示通不通、多快。

只发一次只读 GET；网址里的 ``{参数}`` 用 ``probe`` 里的样例值（没写就用下面的默认样例）。
网络请求走注入的 ``fetch_status``（测试换成假的，不联网）。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from roundtable.core.benchkit.research import ARXIV, S2
from roundtable.core.benchkit.scout import DSH_INDEX, GITHUB

# 网址里常见参数的样例值（东京、欧元换人民币、地图第 1 级的一块）
SAMPLE = {
    "q": "东京",
    "lat": "35.68",
    "lng": "139.76",
    "from": "EUR",
    "to": "CNY",
    "z": "1",
    "x": "1",
    "y": "1",
}
RESEARCH = (
    ("github", "GitHub 搜项目", f"{GITHUB}/search/repositories?q=map&per_page=1", "GitHub"),
    ("arxiv", "查论文", f"{ARXIV}?search_query=all:map&max_results=1", "arXiv"),
    ("s2", "查论文", f"{S2}?query=map&limit=1&fields=title", "Semantic Scholar"),
    ("dsh-market", "找 dsh 插件", DSH_INDEX, "dsh 插件集市"),
)

# 返回 (HTTP 状态码, 说明)；连不上时抛异常
FetchStatus = Callable[[str], Awaitable[int]]


def fill(url: str, probe: dict[str, str]) -> str:
    def sub(m: re.Match[str]) -> str:
        key = m.group(1)
        return quote(str(probe.get(key, SAMPLE.get(key, "1"))), safe="")

    return re.sub(r"\{(\w+)\}", sub, url)


async def _one(
    name: str, use: str, url: str, source: str, group: str, fetch: FetchStatus
) -> dict[str, Any]:
    t0 = time.monotonic()
    try:
        code = await fetch(url)
        ok, detail = code < 400, f"HTTP {code}"
        if code == 429:
            detail += "（被限流，稍后再试）"
        elif code in (403, 407):
            detail += "（被网络或代理拦下）"
    except Exception as exc:  # noqa: BLE001 - 断网、超时、证书：都算不通，写清哪一类
        ok, detail = False, f"连不上（{type(exc).__name__}）"
    return {
        "name": name,
        "use": use,
        "source": source,
        "group": group,
        "ok": ok,
        "detail": detail,
        "ms": int((time.monotonic() - t0) * 1000),
    }


async def net_check(services: list[Any], fetch: FetchStatus) -> list[dict[str, Any]]:
    """成品用的公开数据（``bench.net``）+ 调研用的接口，并发各试一次。"""
    jobs = [
        _one(s.name, s.use, fill(s.url, s.probe), s.source, "成品数据", fetch) for s in services
    ]
    jobs += [_one(n, u, url, src, "调研", fetch) for n, u, url, src in RESEARCH]
    return list(await asyncio.gather(*jobs))


def http_status(timeout_s: float = 8.0) -> FetchStatus:
    import httpx

    async def fetch(url: str) -> int:
        async with httpx.AsyncClient(
            timeout=timeout_s,
            follow_redirects=True,
            headers={"User-Agent": "roundtable-bench-netcheck"},
        ) as c:
            return (await c.get(url)).status_code

    return fetch
