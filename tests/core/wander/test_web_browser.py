"""真浏览器冒烟测试：本机起一个小网站，PlaywrightWeb 打开、读字、跟链接、截图、返回、拦外站。

没有安装 Playwright 或浏览器时跳过（和 tests/web 一样，可用 ROUNDTABLE_CHROMIUM 指定 Chromium）。
"""

from __future__ import annotations

import functools
import http.server
import os
import threading
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")

from roundtable.core.wander import PlaywrightWeb, WebError  # noqa: E402

PAGES = {
    "index.html": '<a href="/v.html">猫咪合集</a><a href="https://example.com/">外站</a>',
    "v.html": "一只橘猫在睡觉",
    "jump.html": "<script>location.href = 'https://example.com/'</script>马上跳走",
}


@pytest.fixture
def site(tmp_path):
    for name, body in PAGES.items():
        title = {"index.html": "首页", "v.html": "猫咪合集"}.get(name, "跳转")
        head = f'<head><meta charset="utf-8"><title>{title}</title></head>'
        html = f"<html>{head}<body>{body}</body></html>"
        (tmp_path / name).write_text(html, encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


async def test_real_browser_reads_follows_and_blocks(site, tmp_path):
    web = PlaywrightWeb(
        channels=[""],
        executable_path=os.environ.get("ROUNDTABLE_CHROMIUM") or None,
        headless=True,
        page_chars=500,
        max_links=10,
        timeout_s=10,
        load_wait_s=0.3,
    )
    try:
        await web.start(tmp_path / "profile" / "V1", ["127.0.0.1"])
    except WebError as e:
        pytest.skip(f"没有可用的浏览器：{e}")
    try:
        home = await web.goto(site)
        assert home.title == "首页" and "猫咪合集" in home.text
        assert home.links == [("猫咪合集", site + "v.html")]  # 外站链接不列出
        video = await web.goto(home.links[0][1])
        assert video.title == "猫咪合集" and "橘猫" in video.text
        shot = await web.screenshot()
        assert shot and shot[:2] == b"\xff\xd8"
        assert (await web.back()).title == "首页"
        with pytest.raises(WebError, match="白名单"):
            await web.goto("https://example.com/")
        # 页面自己用脚本跳去外站：被拦下，读到的永远不是外站的内容
        try:
            jumped = await web.goto(site + "jump.html")
        except WebError:
            jumped = None
        if jumped is not None:
            assert jumped.url.startswith(site) and "Example Domain" not in jumped.text
    finally:
        await web.close()
    assert Path(tmp_path / "profile" / "V1").is_dir()  # 每位成员一个资料夹
