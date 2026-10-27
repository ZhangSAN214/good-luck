"""前端：终端首页的多时区时钟与公告栏。

时钟：增删城市、改名、12 / 24 小时、记在本机；公告栏：未读数、展开算已读、全部已读、记在本机。
"""

from __future__ import annotations

import yaml

LAND_HUB = "try { localStorage.setItem('roundtable.landing', 'hub') } catch {}"


def _names(page) -> list[str]:
    return page.eval_on_selector_all("#hub .ck-face .ck-name", "els => els.map(e => e.textContent)")


def test_clock_shows_two_zones_and_can_be_changed(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .ck-face")
    assert _names(page) == ["曼城", "中国"]
    assert page.get_attribute("#hub .ck-face:nth-child(2)", "data-tz") == "Asia/Shanghai"
    assert "+" in page.inner_text("#hub .ck-face:nth-child(2) .ck-sub")  # 与本机的时差

    page.click("#hub .ck-btn")
    page.wait_for_selector("#hub .ck-panel:not([hidden])")
    page.click("#hub [data-add]")
    assert _names(page) == ["曼城", "中国", "香港"]
    assert page.is_disabled("#hub [data-add]")  # 最多三个

    page.select_option("#hub [data-tz='2']", "Asia/Tokyo")
    assert _names(page)[2] == "东京"  # 默认名跟着时区换
    page.fill("#hub [data-name='0']", "家")
    page.press("#hub [data-name='0']", "Tab")
    page.click("#hub [data-h12='1']")
    assert any(w in page.inner_text("#hub .ck-face:first-child .ck-time") for w in ("上午", "下午"))
    page.click("#hub [data-del='1']")
    assert _names(page) == ["家", "东京"]

    page.keyboard.press("Escape")
    assert page.is_hidden("#hub .ck-panel")
    # 首页每分钟重画、刷新页面后都还在
    page.reload()
    page.wait_for_selector("#hub .ck-face")
    assert _names(page) == ["家", "东京"]
    assert page.get_attribute("#hub .ck-face:nth-child(2)", "data-tz") == "Asia/Tokyo"


def test_notice_board_marks_read(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    total = len(yaml.safe_load(srv.env.rt.changelog.read_text("utf-8"))["releases"])
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .nt-btn.has-new")
    count = page.inner_text("#hub .nt-dot")
    assert count == (str(total) if total <= 9 else "9+")

    page.click("#hub .nt-btn")
    page.wait_for_selector("#hub .nt-panel:not([hidden]) details[open]")
    # 最新的一条默认展开、带「新」，打开面板就算看过
    first = page.locator("#hub .nt-rel").first
    assert first.locator(".nt-new").count() == 1
    assert page.inner_text("#hub .nt-dot") == (str(total - 1) if total - 1 <= 9 else "9+")
    # 默认只列最近 5 条，更早的折叠
    assert page.locator("#hub .nt-rel").count() == min(total, 5)
    if total > 5:
        page.click("#hub [data-more]")
        assert page.locator("#hub .nt-rel").count() == total

    page.locator("#hub .nt-rel summary").nth(1).click()
    want = str(total - 2) if total - 2 <= 9 else "9+"
    page.wait_for_function(f"document.querySelector('#hub .nt-dot').textContent === '{want}'")
    assert page.locator("#hub .nt-rel[open]").count() == 2  # 展开的条目不会被重新读取收起
    page.click("#hub [data-allread]")
    assert page.is_hidden("#hub .nt-dot") and page.locator("#hub .nt-new").count() == 0

    page.reload()
    page.wait_for_selector("#hub .nt-btn")
    page.wait_for_function(
        "document.querySelector('#hub .nt-btn').getAttribute('aria-label').startsWith('公告：版本')"
    )
    assert page.is_hidden("#hub .nt-dot")
