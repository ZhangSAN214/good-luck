"""星图：顶栏打开，圆桌星图有星区和缺口；点缺口出卡片；切到成员的人生星图；时间轴能拖。"""

from __future__ import annotations


def test_starmap(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#settingsbtn")  # 「星图」收进了设置的常用工具
    page.click("#starmapbtn")
    page.wait_for_selector("#starmapdlg[open] .sm-groups li")
    assert page.inner_text("#starmapdlg [data-title]") == "圆桌星图"
    assert page.locator("#starmapdlg .sm-g").count() >= 7
    assert page.locator("#starmapdlg canvas").count() >= 1
    page.locator("#starmapdlg .sm-gaps li").first.click()
    page.wait_for_selector("#starmapdlg .sm-card:not([hidden])")
    tabs = page.locator("#starmapdlg .sm-tabs button")
    assert tabs.count() > 1
    tabs.nth(1).click()
    page.wait_for_function(
        "document.querySelector('#starmapdlg [data-title]').textContent.includes('人生星图')"
    )
    assert page.locator("#starmapdlg .sm-score").count() == 1
    assert page.locator("#starmapdlg .sm-g").count() == 10
    # 什么都没导入的成员：除了程序算的情绪，其余九个星域都是缺口
    assert page.locator("#starmapdlg .sm-gaps li").count() == 9
    page.locator("#starmapdlg .sm-gaps li").first.click()
    assert "怎么补" in page.inner_text("#starmapdlg .sm-card")
    page.click("#starmapdlg [data-close]")
    assert not page.locator("#starmapdlg[open]").count()
