"""前端：插件榜页签按价值排名展示，可以按状态筛选。"""

from __future__ import annotations


def test_plugin_board_tab_lists_and_filters(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click('#tabs button[data-t="plugins"]')
    page.wait_for_selector("#board li")
    first = page.inner_text("#board li:first-child")
    assert first.startswith("1.") and "对圆桌" in first
    total = page.locator("#board li").count()
    page.click('#board-filter [data-f="rejected"]')
    page.wait_for_selector('#board-filter [data-f="rejected"][aria-pressed="true"]')
    statuses = page.eval_on_selector_all("#board li", "els => els.map(e => e.dataset.status)")
    assert statuses and set(statuses) == {"rejected"} and len(statuses) < total
    assert "门槛" in page.inner_text("#pbody")
