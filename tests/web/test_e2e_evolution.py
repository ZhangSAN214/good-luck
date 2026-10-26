"""前端：革新例会入口——默认关闭时说明怎么开，「现在开一场」按钮不可用。"""

from __future__ import annotations


def test_evolution_dialog_when_off(serve, page):
    srv = serve()
    page.goto(srv.url)
    page.click("#evolve")
    page.wait_for_selector("#evolvedlg[open] .ev")
    text = page.inner_text("#evolvedlg")
    assert "没有开启" in text and "evolution.enabled" in text
    assert "收件箱里还没有资料卡" in text and "还没开过" in text
    assert page.locator("#evolvedlg [data-meet]").is_disabled()
    page.click("#evolvedlg [data-close]")
    assert page.locator("#evolvedlg").is_hidden()
