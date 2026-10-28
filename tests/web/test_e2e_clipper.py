"""资料浏览器（端到端）：办公室里打开网页看正文、选段 / 全文存进资料夹、附上到提问区、搜索。"""

from __future__ import annotations

import pytest

from roundtable.core import clipper
from roundtable.core.clipper import Fetched
from roundtable.core.search import SearchService
from roundtable.core.search.fake import FakeSearch

PAGE = """<html><head><title>曼彻斯特指南</title></head><body><nav>导航</nav>
<article><h1>曼彻斯特</h1><p>曼彻斯特是英格兰西北部的城市。</p>
<p>这里有两支著名的足球俱乐部。</p><a href="https://a.test/more">更多</a></article></body></html>"""


@pytest.fixture
def fake_web(monkeypatch):
    opened: list[str] = []

    async def fetch(url, timeout_s, max_bytes):
        opened.append(url)
        return Fetched(url, "text/html; charset=utf-8", PAGE.encode())

    monkeypatch.setattr(clipper, "http_fetch", fetch)
    return opened


def with_search(env) -> None:
    env.rt.search = SearchService({"fake-search": FakeSearch()})


def test_open_save_and_attach(serve, page, fake_web):
    srv = serve(setup=with_search)
    page.goto(srv.url)
    page.click("#clipbtn")
    page.wait_for_selector("#clipdlg[open] #cl-q")
    page.fill("#cl-q", "a.test/manchester")
    page.press("#cl-q", "Enter")
    page.wait_for_selector("#cl-read p")
    assert fake_web == ["https://a.test/manchester"]
    text = page.inner_text("#cl-read")
    assert "英格兰西北部" in text and "导航" not in text
    assert page.inner_text(".cl-title") == "曼彻斯特指南"

    # 选中第二段 → 存选中
    page.evaluate(
        """() => { const p = document.querySelectorAll('#cl-read p')[1];
        const r = document.createRange(); r.selectNodeContents(p);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r); }"""
    )
    page.wait_for_selector("[data-save-sel]:not([disabled])")
    page.click("[data-save-sel]")
    page.wait_for_selector(".cl-msg:has-text('存好了')")
    page.click("[data-save-all]")
    page.wait_for_selector("[data-tab='saved']:has-text('2')")

    page.click("[data-tab='saved']")
    rows = page.locator(".cl-list .grp-row")
    assert rows.count() == 2 and "选段" in rows.nth(1).inner_text()
    rows.nth(1).locator("[data-attach]").click()
    page.wait_for_selector(".cl-msg:has-text('已附上')")
    page.click("#clipdlg [data-close]")
    page.wait_for_selector("#attachments:has-text('曼彻斯特指南.md')")

    # 资料正文：只有选中的那段
    clips = srv.env.rt.repo.setting(clipper.CLIPS_KEY)
    assert clips and "partial" in clips


def test_search_then_open_hit_and_back(serve, page, fake_web):
    srv = serve(setup=with_search)
    page.goto(srv.url)
    page.click("#clipbtn")
    page.fill("#cl-q", "曼彻斯特 天气")
    page.press("#cl-q", "Enter")
    page.wait_for_selector(".cl-hit")
    assert page.locator(".cl-hit").count() == 3
    page.locator(".cl-hit").first.click()
    page.wait_for_selector("#cl-read p")
    assert fake_web[0].startswith("https://example.test/")
    page.click("[data-back]")
    page.wait_for_selector(".cl-hit")
    assert srv.env.fake.calls == []  # 搜索和打开网页都不调用对话模型
