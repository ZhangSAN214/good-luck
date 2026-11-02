"""「出问题点这里」：点开就自检，按现场筛选，生成体检报告。"""

from __future__ import annotations


def test_firstaid_panel(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#firstaidbtn")
    page.wait_for_selector("#firstaiddlg[open] .fa-head")
    assert page.locator("#firstaiddlg .fa-scene").count() == 4
    total = page.locator("#firstaiddlg .fa-item").count()
    assert total > 3
    page.click("#firstaiddlg .fa-scene[data-scene=undo]")
    assert page.locator("#firstaiddlg .fa-item").count() < total
    page.click("#firstaiddlg [data-report]")
    page.wait_for_selector("#firstaiddlg .fa-report")
    assert "圆桌体检报告" in page.input_value("#firstaiddlg .fa-report")
    page.click("#firstaiddlg [data-close]")
    assert not page.locator("#firstaiddlg[open]").count()
