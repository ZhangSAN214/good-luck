"""成员的隔离浏览器（docs/WANDER.md）：只打开白名单网站，读页面文字和链接，截一张小图给你看。

- 用 Playwright 驱动电脑上的 Edge / Chrome（``channels`` 依次试，空字符串 = Playwright 自带的
  Chromium），每位成员一个独立资料夹（``data/wander/<成员>/``，登录状态在里面，不进 git），
  和你自己的浏览器完全分开。
- 只看不动：只按链接地址打开页面、按搜索地址模板搜索；不点按钮、不填表、不下载。
  页面跳到白名单以外的网站会被拦下（图片、脚本等资源不拦，否则网页打不开）。
- Playwright 的同步接口放在一个专用线程里跑（同一个浏览器对象只能在同一个线程里用）。
- 测试用 ``FakeWeb``，不开浏览器、不联网。
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

LINKS_JS = """() => Array.from(document.querySelectorAll('a[href]')).map(a => [
  (a.innerText || a.title || a.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ')
    .slice(0, 60),
  a.href,
])"""


class WebError(Exception):
    """浏览器打不开、页面加载失败等（文字已脱敏，可以直接给用户看）。"""


@dataclass
class Snapshot:
    url: str
    title: str
    text: str
    links: list[tuple[str, str]] = field(default_factory=list)  # (链接文字, 地址)


def host_allowed(url: str, hosts: list[str] | tuple[str, ...]) -> bool:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in hosts)


def tidy(text: str, limit: int) -> str:
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in (text or "").splitlines()]
    out = "\n".join(ln for ln in lines if ln)
    return out[:limit]


def keep_links(
    raw: list[Any], hosts: list[str] | tuple[str, ...], limit: int
) -> list[tuple[str, str]]:
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for item in raw or []:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        text, url = str(item[0] or "").strip(), str(item[1] or "").split("#")[0]
        if len(text) < 2 or url in seen or not host_allowed(url, hosts):
            continue
        seen.add(url)
        out.append((text, url))
        if len(out) >= limit:
            break
    return out


class Web(Protocol):
    async def start(self, profile: Path, hosts: list[str]) -> None: ...

    async def goto(self, url: str) -> Snapshot: ...

    async def back(self) -> Snapshot: ...

    async def screenshot(self) -> bytes | None: ...

    async def close(self) -> None: ...


class PlaywrightWeb:
    """真的浏览器。每次出门 start 一次、close 一次。"""

    def __init__(
        self,
        *,
        channels: list[str],
        headless: bool,
        page_chars: int,
        max_links: int,
        timeout_s: float,
        load_wait_s: float,
        executable_path: str | None = None,
    ) -> None:
        """executable_path：channel 为空时用的 Chromium 位置（只在测试里指定）。"""
        self.channels = channels
        self.executable_path = executable_path
        self.headless = headless
        self.page_chars = page_chars
        self.max_links = max_links
        self.timeout_ms = timeout_s * 1000
        self.load_wait_ms = load_wait_s * 1000
        self.hosts: list[str] = []
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wander")
        self._pw: Any = None
        self._ctx: Any = None
        self._page: Any = None

    @staticmethod
    def available() -> str | None:
        """能用时返回 None，否则返回原因。"""
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError:
            return "没有安装 Playwright（pip install playwright）"
        return None

    async def _run(self, fn: Any, *args: Any) -> Any:
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    # --- 以下在专用线程里执行 -------------------------------------------------------------

    def _start(self, profile: Path) -> None:
        from playwright.sync_api import sync_playwright

        profile.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        errors: list[str] = []
        for channel in self.channels:
            try:
                self._ctx = self._pw.chromium.launch_persistent_context(
                    str(profile),
                    channel=channel or None,
                    executable_path=None if channel else self.executable_path,
                    headless=self.headless,
                    viewport={"width": 1280, "height": 800},
                    accept_downloads=False,
                    locale="zh-CN",
                )
                break
            except Exception as exc:  # noqa: BLE001 - 这个浏览器没有就试下一个
                errors.append(str(exc).splitlines()[0][:80] if str(exc) else type(exc).__name__)
        if self._ctx is None:
            self._pw.stop()
            self._pw = None
            raise WebError(
                "找不到能用的浏览器（Edge / Chrome）。" + (f"（{errors[-1]}）" if errors else "")
            )
        self._ctx.set_default_timeout(self.timeout_ms)
        self._ctx.route("**/*", self._guard)
        pages = self._ctx.pages
        self._page = pages[0] if pages else self._ctx.new_page()

    def _guard(self, route: Any) -> None:
        """页面（含内嵌框架）跳去白名单以外的网站时拦下；图片、脚本等资源放行。"""
        req = route.request
        if req.is_navigation_request() and not host_allowed(req.url, self.hosts):
            route.abort("blockedbyclient")
            return
        route.continue_()

    def _snap(self) -> Snapshot:
        page = self._page
        if self.load_wait_ms:
            page.wait_for_timeout(self.load_wait_ms)
        if not host_allowed(page.url, self.hosts):
            # 页面自己跳去外站被拦下后停在出错页：退回上一页
            with contextlib.suppress(Exception):
                page.go_back(wait_until="domcontentloaded")
            if not host_allowed(page.url, self.hosts):
                raise WebError("页面跳到了白名单以外的网站，已拦下")
        try:
            text = page.inner_text("body")
        except Exception:  # noqa: BLE001 - 页面还没好就当没字
            text = ""
        try:
            raw = page.evaluate(LINKS_JS)
        except Exception:  # noqa: BLE001
            raw = []
        return Snapshot(
            page.url,
            (page.title() or "")[:100],
            tidy(text, self.page_chars),
            keep_links(raw, self.hosts, self.max_links),
        )

    def _goto(self, url: str) -> Snapshot:
        try:
            self._page.goto(url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            raise WebError(f"页面没打开：{str(exc).splitlines()[0][:80]}") from None
        return self._snap()

    def _back(self) -> Snapshot:
        try:
            self._page.go_back(wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            raise WebError(f"没能返回：{str(exc).splitlines()[0][:80]}") from None
        return self._snap()

    def _shot(self) -> bytes | None:
        try:
            return self._page.screenshot(type="jpeg", quality=55)
        except Exception:  # noqa: BLE001 - 截不到就不显示
            return None

    def _close(self) -> None:
        for obj, how in ((self._ctx, "close"), (self._pw, "stop")):
            if obj is not None:
                with contextlib.suppress(Exception):  # 关不掉也不影响下次
                    getattr(obj, how)()
        self._ctx = self._pw = self._page = None

    # --- 对外（异步） --------------------------------------------------------------------

    async def start(self, profile: Path, hosts: list[str]) -> None:
        self.hosts = list(hosts)
        await self._run(self._start, profile)

    async def goto(self, url: str) -> Snapshot:
        if not host_allowed(url, self.hosts):
            raise WebError("这个网址不在白名单里")
        return await self._run(self._goto, url)

    async def back(self) -> Snapshot:
        return await self._run(self._back)

    async def screenshot(self) -> bytes | None:
        return await self._run(self._shot)

    async def close(self) -> None:
        await self._run(self._close)
        self._pool.shutdown(wait=False)


class FakeWeb:
    """测试用：pages 是 网址 → 页面。打不开的网址抛 WebError。"""

    def __init__(self, pages: dict[str, Snapshot], *, fail_start: str | None = None) -> None:
        self.pages = pages
        self.fail_start = fail_start
        self.visited: list[str] = []
        self.history: list[str] = []
        self.profile: Path | None = None
        self.hosts: list[str] = []
        self.closed = False

    async def start(self, profile: Path, hosts: list[str]) -> None:
        if self.fail_start:
            raise WebError(self.fail_start)
        self.profile, self.hosts = profile, list(hosts)

    async def goto(self, url: str) -> Snapshot:
        if not host_allowed(url, self.hosts):
            raise WebError("这个网址不在白名单里")
        self.visited.append(url)
        page = self.pages.get(url)
        if page is None:
            raise WebError("页面没打开：404")
        self.history.append(url)
        return page

    async def back(self) -> Snapshot:
        if len(self.history) < 2:
            raise WebError("没能返回：没有上一页")
        self.history.pop()
        return self.pages[self.history[-1]]

    async def screenshot(self) -> bytes | None:
        return b"\xff\xd8fake-jpeg"

    async def close(self) -> None:
        self.closed = True
