"""游戏工坊做出来的游戏：机器先查一遍，查出的问题交回给助手修（不调模型、不花钱）。

- ``static_issues``：只看文件内容（有没有、多大、有没有脚本、有没有引外部文件、有没有弹窗）。
- ``browser_issues``：在本机浏览器里打开（手机宽、电脑宽各一次），点一下、按几个键，
  记下报错、横向溢出、白屏。只放行本机，其余网址一律中止。没装 Playwright 时返回 None（跳过）。
"""

from __future__ import annotations

import contextlib
import functools
import os
import re
from pathlib import Path

GAME_FILE = "game.html"
META_FILE = "game.json"

# 外部资源：src / href / url() / import 指向 http(s) 或 //
_EXTERNAL = re.compile(
    r"""(?:\b(?:src|href)\s*=\s*["']?\s*(?:https?:)?//)|(?:url\(\s*["']?\s*(?:https?:)?//)"""
    r"""|(?:\bimport\s*(?:[\w{}*,\s]+from\s*)?["'](?:https?:)?//)""",
    re.IGNORECASE,
)
_NET = re.compile(r"""\b(?:fetch|EventSource|WebSocket)\s*\(\s*["'`](?:https?|wss?):""")
_POPUP = re.compile(r"(?<![\w.])(?:window\.)?(?:alert|confirm|prompt)\s*\(")

# 在页面里量：横向溢出、有没有画东西
_PROBE_JS = """() => {
  const doc = document.documentElement;
  const over = doc.scrollWidth > window.innerWidth + 2;
  const canvases = [...document.querySelectorAll('canvas')].filter(c => c.width && c.height);
  const text = (document.body?.innerText || '').trim().length;
  const shown = [...document.body?.querySelectorAll('*') || []].some(e => {
    const r = e.getBoundingClientRect();
    return r.width > 40 && r.height > 40;
  });
  return {over, canvases: canvases.length, text, shown};
}"""

VIEWPORTS = (("手机", 390, 780), ("电脑", 1280, 800))


def static_issues(folder: Path, max_bytes: int) -> list[str]:
    path = folder / GAME_FILE
    if not path.is_file():
        return [f"没有做出 {GAME_FILE}"]
    size = path.stat().st_size
    if size > max_bytes:
        return [f"{GAME_FILE} 太大了（{size // 1024} KB，最多 {max_bytes // 1024} KB）"]
    html = path.read_text(encoding="utf-8", errors="replace")
    issues = []
    if "<script" not in html.lower():
        issues.append("页面里没有脚本，游戏动不起来")
    if not re.search(r"<meta[^>]+charset", html, re.IGNORECASE):
        issues.append('没有写 <meta charset="utf-8">，中文可能乱码')
    if not re.search(r"<meta[^>]+viewport", html, re.IGNORECASE):
        issues.append("没有写 viewport，手机上会缩得很小")
    if _EXTERNAL.search(html):
        issues.append("引用了外部文件或网址（要全部写在文件里，不联网）")
    if _NET.search(html):
        issues.append("页面会去联网（游戏里不允许）")
    if _POPUP.search(html):
        issues.append("用了 alert / confirm / prompt 弹窗（沙箱里会被拦下，改成页面里的提示）")
    return issues


def browser_issues(folder: Path, settle_ms: int = 900) -> list[str] | None:
    """在本机浏览器里试一下。没装 Playwright 或浏览器起不来时返回 None（跳过，不算问题）。"""
    try:
        from playwright.sync_api import Error as PwError
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    from roundtable.core.benchkit.quality import _gate, _serve

    issues: list[str] = []
    with contextlib.ExitStack() as stack:
        base = stack.enter_context(_serve(folder.resolve()))
        url = f"{base}/{GAME_FILE}"
        try:
            pw = stack.enter_context(sync_playwright())
            browser = pw.chromium.launch(
                executable_path=os.environ.get("ROUNDTABLE_CHROMIUM") or None
            )
        except PwError:
            return None
        stack.callback(browser.close)
        for where, w, h in VIEWPORTS:
            page = browser.new_page(viewport={"width": w, "height": h}, has_touch=w < 600)
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.on(
                "console",
                lambda m, errors=errors: errors.append(m.text) if m.type == "error" else None,
            )
            page.on("dialog", lambda d: d.dismiss())
            page.route("**/*", functools.partial(_gate, ("127.0.0.1", "localhost"), ()))
            try:
                page.goto(url, wait_until="load", timeout=20000)
                page.wait_for_timeout(settle_ms)
                page.mouse.click(w / 2, h / 2)
                for key in ("Enter", "Space", "ArrowRight", "ArrowUp", "KeyD", "KeyW"):
                    page.keyboard.press(key)
                    page.wait_for_timeout(60)
                buttons = page.locator("button:visible")
                if buttons.count():
                    with contextlib.suppress(PwError):
                        buttons.first.click(timeout=1500)
                page.wait_for_timeout(settle_ms)
                probe = page.evaluate(_PROBE_JS)
            except PwError as exc:
                issues.append(f"{where}宽度：页面打不开（{str(exc).splitlines()[0][:120]}）")
                page.close()
                continue
            page.close()
            errs = [e for e in errors if "Failed to load resource" not in e]
            for e in dict.fromkeys(errs):
                issues.append(f"{where}宽度：页面报错 {e[:200]}")
            if probe.get("over"):
                issues.append(f"{where}宽度：页面比屏幕宽，出现横向滚动条")
            if not probe.get("canvases") and not probe.get("shown") and not probe.get("text"):
                issues.append(f"{where}宽度：页面是空白的")
    return list(dict.fromkeys(issues))[:12]
