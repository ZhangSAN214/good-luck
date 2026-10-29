"""资料浏览器：网址规范化与内网拦截、重定向逐跳检查、正文提取、编码、存资料与附上、搜索计费。"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest

from roundtable.core import clipper
from roundtable.core.clipper import ClipError, Fetched, extract_page
from roundtable.core.search import SearchService
from roundtable.core.search.fake import FakeSearch
from roundtable.core.service import RoundtableService, ServiceError

from .orchestrator.conftest import Env

ARTICLE = """<html><head><title>站名 - 文章</title>
<meta property="og:title" content="好标题"></head><body>
<nav>首页 菜单</nav><header>站头</header>
<div class="side">侧栏广告</div>
<article><h1>大标题</h1>
<p>第一段&nbsp;内容，<a href="/next">下一篇</a>很长很长的正文。</p>
<script>var secret = 1</script><style>p{}</style>
<p>第二段<br>换行</p><ul><li>要点一</li><li>要点二</li></ul>
<p hidden>隐藏的</p><p>第一段&nbsp;内容，<a href="/next">下一篇</a>很长很长的正文。</p>
<a href="javascript:alert(1)">坏链接</a>
</article><footer>版权所有</footer></body></html>"""


async def public(host: str) -> list[str]:
    return ["93.184.216.34"]


def run(coro):
    return asyncio.run(coro)


# --- 网址 ----------------------------------------------------------------------


def test_normalize_url():
    assert clipper.normalize_url(" example.com/a ") == "https://example.com/a"
    for bad in ("", "ftp://x.com", "javascript:alert(1)", "https://u:p@x.com/"):
        with pytest.raises(ClipError):
            clipper.normalize_url(bad)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost:8000/api",
        "http://router.local/",
        "http://10.0.0.2/",
        "http://192.168.1.5/",
        "http://172.16.0.1/",
        "http://100.64.1.1/",  # Tailscale 地址
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
    ],
)
def test_private_addresses_blocked(url):
    with pytest.raises(ClipError, match="内网"):
        run(clipper.check_public(url, public))


def test_dns_to_private_blocked_and_fake_ip_allowed():
    async def to_lan(host):
        return ["192.168.0.10"]

    async def fake_ip(host):
        return ["198.18.0.7"]

    with pytest.raises(ClipError, match="内网"):
        run(clipper.check_public("https://evil.test/", to_lan))
    run(clipper.check_public("https://ok.test/", fake_ip))
    run(clipper.check_public("https://ok.test/", public))


def test_redirect_to_private_is_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pub.test":
            return httpx.Response(302, headers={"location": "http://127.0.0.1:8000/api/status"})
        return httpx.Response(200, text="内网的东西")

    transport = httpx.MockTransport(handler)
    with pytest.raises(ClipError, match="内网"):
        run(clipper.http_fetch("https://pub.test/", 5, 10_000, resolve=public, transport=transport))


def test_fetch_follows_redirect_and_limits():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        if request.url.path == "/big":
            return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "text/html"})
        if request.url.path == "/img":
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        if request.url.path == "/login":
            return httpx.Response(403)
        return httpx.Response(200, text=ARTICLE, headers={"content-type": "text/html"})

    t = httpx.MockTransport(handler)
    got = run(clipper.http_fetch("https://a.test/old", 5, 100_000, resolve=public, transport=t))
    assert got.url == "https://a.test/new" and b"<article>" in got.data
    with pytest.raises(ClipError, match="太大"):
        run(clipper.http_fetch("https://a.test/big", 5, 1000, resolve=public, transport=t))
    with pytest.raises(ClipError, match="不是网页"):
        run(clipper.http_fetch("https://a.test/img", 5, 1000, resolve=public, transport=t))
    with pytest.raises(ClipError, match="403"):
        run(clipper.http_fetch("https://a.test/login", 5, 1000, resolve=public, transport=t))


# --- 正文提取 ------------------------------------------------------------------


def test_extract_article_only():
    page = extract_page(ARTICLE, "https://www.a.test/post")
    assert page.title == "好标题" and page.site == "a.test"
    assert page.blocks == [
        "## 大标题",
        "第一段 内容，下一篇很长很长的正文。",
        "第二段\n换行",
        "要点一",
        "要点二",
        "坏链接",  # 文字留着，javascript: 链接不要
    ]
    text = "\n".join(page.blocks)
    for junk in ("菜单", "站头", "侧栏", "secret", "版权", "隐藏"):
        assert junk not in text
    assert page.links == [{"text": "下一篇", "url": "https://www.a.test/next"}]


def test_extract_without_article_and_limits():
    html = "<title>无文章</title><body><div><p>" + "很长的一段。" * 50 + "</p><p>第二段</p></div>"
    page = extract_page(html, "https://b.test/")
    assert page.title == "无文章" and len(page.blocks) == 2
    cut = extract_page(html, "https://b.test/", max_chars=100)
    assert cut.blocks == ["（后面还有内容，太长没有显示）"]
    broken = extract_page("<p>没闭合 <b>粗体<p>下一段", "https://b.test/")
    assert broken.blocks == ["没闭合 粗体", "下一段"]


def test_decode_charsets_and_plain_text():
    gbk = "<meta charset='gbk'><p>中文网页</p>".encode("gbk")
    assert "中文网页" in clipper.decode(gbk, "text/html")
    assert "中文网页" in clipper.decode(gbk, "text/html; charset=GB2312")
    assert clipper.decode(b"\xff ok", "text/html; charset=bogus").endswith("ok")
    page = clipper.parse_fetched(
        Fetched("https://c.test/a.txt", "text/plain", "第一段\n\n第二段".encode()), max_chars=1000
    )
    assert page.blocks == ["第一段", "第二段"]


# --- 存资料、附上、搜索（服务层） --------------------------------------------------


def test_save_attach_delete_round_trip():
    env = Env()
    service = RoundtableService(env.rt)
    saved = service.clip_save(
        "https://a.test/post", "好标题", "正文第一段\n\n正文第二段", note="备用"
    )
    assert saved["title"] == "好标题" and saved["chars"] > 0 and not saved["truncated"]
    assert "key" not in saved  # 不暴露存储位置
    listed = service.clips()
    assert [c["id"] for c in listed["clips"]] == [saved["id"]]
    body = service.clip_text(saved["id"])["text"]
    assert body.startswith("# 好标题") and "来源：https://a.test/post" in body
    assert "备注：备用" in body and "正文第二段" in body
    # 附上两次 = 两个不同的附件（附件只能用一次）
    a1, a2 = service.clip_attach(saved["id"]), service.clip_attach(saved["id"])
    assert a1["id"] != a2["id"] and a1["name"] == "好标题.md" and a1["kind"] == "text"
    service.clip_delete(saved["id"])
    assert service.clips()["clips"] == []
    with pytest.raises(ServiceError):
        service.clip_attach(saved["id"])


def test_save_limits():
    rules = Env().rt.config.roundtable.clipper
    env = Env(
        rt_update={"clipper": rules.model_copy(update={"max_clip_chars": 500, "max_clips": 2})}
    )
    service = RoundtableService(env.rt)
    long = service.clip_save("a.test/x", "", "字" * 900)
    assert long["truncated"] and long["chars"] == 500 and long["title"] == "a.test"
    for i in range(3):
        service.clip_save("https://a.test/", f"第{i}篇", "内容")
    assert [c["title"] for c in service.clips()["clips"]] == ["第2篇", "第1篇"]
    with pytest.raises(ServiceError):
        service.clip_save("https://a.test/", "空", "   ")
    with pytest.raises(ServiceError):
        service.clip_save("ftp://a.test/", "x", "内容")
    assert clipper.safe_name('a/b:c*"d') == "a b c d.md"


def test_open_uses_fetch(monkeypatch):
    seen = []

    async def fetch(url, timeout_s, max_bytes):
        seen.append(url)
        if "empty" in url:
            return Fetched(url, "text/html", b"<script>app()</script>")
        return Fetched(url, "text/html; charset=utf-8", ARTICLE.encode())

    monkeypatch.setattr(clipper, "http_fetch", fetch)
    service = RoundtableService(Env().rt)
    page = run(service.clip_open("a.test/post"))
    assert seen == ["https://a.test/post"] and page["title"] == "好标题" and "hint" not in page
    empty = run(service.clip_open("https://empty.test/"))
    assert empty["blocks"] == [] and "登录" in empty["hint"]
    rules = Env().rt.config.roundtable.clipper
    env = Env(rt_update={"clipper": rules.model_copy(update={"enabled": False})})
    with pytest.raises(ServiceError, match="关闭"):
        run(RoundtableService(env.rt).clip_open("a.test"))


def test_search_records_cost_and_checks_budget():
    env = Env()
    fake = FakeSearch()
    env.rt.search = SearchService({"fake-search": fake})
    service = RoundtableService(env.rt)
    out = run(service.clip_search("曼彻斯特 天气"))
    assert fake.queries == ["曼彻斯特 天气"] and len(out["hits"]) == 3
    assert out["hits"][0]["url"].startswith("https://example.test/")
    env.rt.repo.chat_session(clipper.SEARCH_ROOM)
    day = env.rt.budget.now()
    start, end = day - timedelta(days=1), day + timedelta(days=1)
    assert env.rt.repo.count_in_steps("chat", (clipper.SEARCH_STEP,), start, end) == 1
    with pytest.raises(ServiceError):
        run(service.clip_search("  "))
