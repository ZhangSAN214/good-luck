"""安卓外壳的连接页（mobile/www/connect.js）：地址解析、依次试地址；页面在真 Chromium 里连接与返回。

纯函数用 node 跑；页面流程用 Playwright，两边没有就各自跳过。
"""

from __future__ import annotations

import functools
import json
import shutil
import subprocess
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from .conftest import wait_until

WWW = Path(__file__).resolve().parents[2] / "mobile" / "www"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")


def run(body: str):
    script = (
        f"const C = await import({json.dumps((WWW / 'connect.js').as_uri())});"
        f"const out = await (async () => {{ {body} }})();"
        "process.stdout.write(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(res.stdout)


@needs_node
def test_normalize_addresses():
    out = run(
        "return ['192.168.1.23', '192.168.1.23:8001', ' http://10.0.0.5:8000/x ',"
        " 'https://pc.tail1234.ts.net', 'ftp://1.2.3.4', 'http://u:p@1.2.3.4', '', 'a b']"
        ".map((s) => C.normalize(s));"
    )
    assert out == [
        {"base": "http://192.168.1.23:8000", "open": "http://192.168.1.23:8000/"},
        {"base": "http://192.168.1.23:8001", "open": "http://192.168.1.23:8001/"},
        {"base": "http://10.0.0.5:8000", "open": "http://10.0.0.5:8000/"},
        {"base": "https://pc.tail1234.ts.net", "open": "https://pc.tail1234.ts.net/"},
        None,
        None,
        None,
        None,
    ]


@needs_node
def test_pair_link_keeps_code_once():
    out = run("return C.normalize('http://192.168.1.23:8000/lan/pair?c=ab%2Fc');")
    assert out == {
        "base": "http://192.168.1.23:8000",
        "open": "http://192.168.1.23:8000/lan/pair?c=ab%2Fc",
    }


@needs_node
def test_pick_tries_home_then_away():
    out = run(
        "const tried = [];"
        "const probe = async (b) => { tried.push(b); return b.includes('100.'); };"
        "const hit = await C.pick([C.normalize('192.168.1.2'), null, C.normalize('100.64.0.9')],"
        " probe);"
        "const none = await C.pick([C.normalize('192.168.1.2')], async () => false);"
        "return [hit.base, tried, none];"
    )
    assert out == [
        "http://100.64.0.9:8000",
        ["http://192.168.1.2:8000", "http://100.64.0.9:8000"],
        None,
    ]


@needs_node
def test_saved_settings_survive_bad_storage():
    out = run(
        "const mem = { v: null, getItem() { return this.v; }, setItem(k, v) { this.v = v; } };"
        "C.save({ home: 'http://192.168.1.2:8000', away: '' }, mem);"
        "const bad = { getItem() { throw new Error('no'); },"
        " setItem() { throw new Error('no'); } };"
        "C.save({ home: 'x' }, bad);"
        "return [C.load(mem), C.load(bad)];"
    )
    assert out == [{"home": "http://192.168.1.2:8000", "away": ""}, {"home": "", "away": ""}]


@pytest.fixture
def www():
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(WWW))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_page_connects_and_back_does_not_loop(browser, www):
    ctx = browser.new_context(viewport={"width": 400, "height": 800})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    try:
        pg.goto(f"{www}/index.html")
        assert pg.locator("#msg").inner_text() == ""
        # 连不上：给出排查提示，不跳走
        pg.fill("#home", "127.0.0.1:9")
        pg.click("#go")
        wait_until(lambda: "连不上" in pg.locator("#msg").inner_text())
        assert pg.url.endswith("/index.html")
        # 家里地址填这台测试服务器：跳过去（这里就是同一个页面，带上返回标记后不再自动连）
        pg.fill("#home", www.removeprefix("http://"))
        pg.click("#go")
        # pg.url 本身不推进事件循环，要用 wait_for_url 等跳转完成
        pg.wait_for_url(f"{www}/", timeout=10_000)
        wait_until(lambda: "改一下" in pg.locator("#msg").inner_text())
        saved = pg.evaluate("localStorage.getItem('roundtable.remote.v1')")
        assert json.loads(saved) == {"home": www, "away": ""}
    finally:
        ctx.close()
    assert errors == [], errors
