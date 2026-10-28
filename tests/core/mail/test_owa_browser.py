"""专用浏览器的真实流程：本机起一个假的 Outlook 网页版，用 Playwright（Chromium）去读。

验证：只读守卫在浏览器里真的拦住写入请求和外站请求；借网页版的请求头补发只读请求；
登录状态过期时识别出来。没有安装 playwright 或浏览器时跳过。不联网。
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from roundtable.core.mail.owa import OwaBrowser, SessionExpired

from .samples import (
    attachments_response,
    ews_item,
    find_response,
    get_response,
    html_response,
    inline_attachment,
)

PNG_B64 = "iVBORw0KGgo="

pytest.importorskip("playwright.sync_api")

HEADERS = (
    "{'x-owa-canary': 'canary-1', 'authorization': 'Bearer tok-1',"
    " 'content-type': 'application/json'}"
)
PAGE = (
    '<!doctype html><title>mail</title><body>inbox<img src="https://evil.example/p.gif">'
    f"<script>const h = {HEADERS};"
    "fetch('/owa/service.svc?action=FindConversation&app=Mail',"
    " {method: 'POST', headers: {...h, action: 'FindConversation'}, body: '{}'});"
    "fetch('/owa/service.svc?action=UpdateItem&app=Mail',"
    " {method: 'POST', headers: {...h, action: 'UpdateItem'}, body: '{}'}).catch(() => {});"
    "</script></body>"
)


class FakeOwa:
    def __init__(self) -> None:
        self.hits: list[str] = []
        self.expired = False
        owa = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json", headers=()):
                data = body.encode() if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("content-type", ctype)
                for k, v in headers:
                    self.send_header(k, v)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = urlsplit(self.path).path
                owa.hits.append(f"GET {path}")
                if path.startswith("/mail"):
                    if owa.expired:
                        self.send_response(302)
                        self.send_header("location", "/login")
                        self.end_headers()
                        return
                    self._send(
                        200, PAGE, "text/html", [("set-cookie", "X-OWA-CANARY=canary-1; Path=/")]
                    )
                else:
                    self._send(200, "<p>sign in</p>", "text/html")

            def do_POST(self):
                parts = urlsplit(self.path)
                action = parse_qs(parts.query).get("action", [""])[0]
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                owa.hits.append(f"POST {action}")
                if action == "FindConversation":  # 网页版自己加载的数据（被动收集）
                    return self._send(200, json.dumps(find_response([ews_item(9)])))
                if self.headers.get("authorization") != "Bearer tok-1":
                    return self._send(401, "{}")
                if action == "FindItem":
                    limit = json.loads(body)["Body"]["Paging"]["MaxEntriesReturned"]
                    items = [
                        ews_item(1, subject="Exam timetable"),
                        ews_item(2, when="2026-10-25T08:00:00Z"),
                    ]
                    return self._send(200, json.dumps(find_response(items[:limit])))
                if action == "GetItem" and b'"HTML"' in body:
                    [i] = [x["Id"] for x in json.loads(body)["Body"]["ItemIds"]]
                    html = f'<p onclick="x()">图文 {i}</p><img src="cid:logo@x"><script>1</script>'
                    atts = [inline_attachment("att-1", "logo@x")]
                    return self._send(200, json.dumps(html_response(i, html, atts)))
                if action == "GetAttachment":
                    return self._send(200, json.dumps(attachments_response({"att-1": PNG_B64})))
                if action == "GetItem":
                    ids = [x["Id"] for x in json.loads(body)["Body"]["ItemIds"]]
                    return self._send(
                        200, json.dumps(get_response({i: f"body of {i}" for i in ids}))
                    )
                self._send(500, "{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def owa():
    server = FakeOwa()
    yield server
    server.server.shutdown()


def browser(tmp_path, owa: FakeOwa) -> OwaBrowser:
    return OwaBrowser(
        tmp_path / "mail_browser",
        base_url=owa.url,
        channels=(None,),
        load_timeout_s=20,
        expire_after_s=2,
        mail_hosts=("127.0.0.1",),
        plain_http=("127.0.0.1",),
        # 与 tests/web 相同：playwright 版本和已装的 Chromium 不匹配时用它指定
        executable_path=os.environ.get("ROUNDTABLE_CHROMIUM") or None,
    )


def _launch_or_skip(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc) or "找不到能用的浏览器" in str(exc):
            pytest.skip("没有安装 Chromium")
        raise


def test_reads_inbox_and_bodies_through_the_guard(tmp_path, owa):
    b = browser(tmp_path, owa)

    def run():
        with b.open(interactive=False) as s:
            return s.items(1), s.bodies(["AAMk/1+="]), list(s.blocked)

    items, bodies, blocked = _launch_or_skip(run)
    assert [i.subject for i in items] == ["Exam timetable"]  # 补发的只读列表请求
    assert bodies == {"AAMk/1+=": "body of AAMk/1+="}
    assert "POST UpdateItem" not in owa.hits  # 写入请求在浏览器里就被拦了
    assert any("UpdateItem" in r for r in blocked) and any("evil.example" in r for r in blocked)
    assert b.has_login()
    b.clear()
    assert not (tmp_path / "mail_browser").exists()


def test_reads_html_body_with_inline_images(tmp_path, owa):
    b = browser(tmp_path, owa)

    def run():
        with b.open(interactive=False) as s:
            return s.html(["AAMk/1+="])

    got = _launch_or_skip(run)
    html, inline = got["AAMk/1+="]
    assert "图文 AAMk/1+=" in html  # 原样带回，清洗在 box 里做
    assert inline == {"logo@x": f"data:image/png;base64,{PNG_B64}"}
    assert "POST GetAttachment" in owa.hits


def test_falls_back_to_what_the_page_loaded(tmp_path, owa, monkeypatch):
    b = browser(tmp_path, owa)
    from roundtable.core.mail import owa as owa_mod

    monkeypatch.setattr(owa_mod, "find_items_request", lambda limit: {"broken": True})

    def run():
        with b.open(interactive=False) as s:
            return s.items(10)

    items = _launch_or_skip(run)
    assert [i.id for i in items] == ["AAMk/9+="]


def test_expired_login_is_detected(tmp_path, owa):
    owa.expired = True
    b = browser(tmp_path, owa)

    def run():
        with b.open(interactive=False):
            pass

    with pytest.raises(SessionExpired):
        _launch_or_skip(run)
