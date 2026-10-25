"""前端：终端首页（默认首页、今日助理、入口跳转、待确认计数）与外观切换。"""

from __future__ import annotations

LAND_HUB = "try { localStorage.setItem('roundtable.landing', 'hub') } catch {}"


def test_hub_is_the_default_landing_and_links_to_every_entry(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    sid = repo.create_session("等你拍板的事", seed=1, anonymous=False)
    repo.set_status(sid, "awaiting_confirmation")
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .hb-hero")
    assert page.is_hidden("#main") and page.url.endswith("#m=hub")
    # 黑底大数字 = 等你拍板的件数；助理台词按同样的数字拼出来
    assert page.inner_text("#hub .hb-num b") == "01"
    assert "1 件事等你拍板" in page.inner_text("#hub .hb-voice")
    assert page.is_disabled("#hub .hb-daily")  # 日常还没开放
    page.click("#hub .hb-home")
    page.wait_for_selector("#home .tenant")
    page.keyboard.press("Alt+Digit4")
    page.wait_for_selector("#hub .hb-chat")
    page.click("#hub .hb-chat")
    page.wait_for_selector("#gchat", state="visible")
    page.click("#entries button[data-m='hub']")
    page.click("#hub .hb-hero")
    page.wait_for_selector("#main", state="visible")


def test_hub_landing_can_be_turned_off(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    # 只在第一次打开时设成终端首页；刷新后看勾选是否生效
    page.add_init_script(
        "try { if (!sessionStorage.getItem('hub-once')) { sessionStorage.setItem('hub-once', '1');"
        " localStorage.setItem('roundtable.landing', 'hub') } } catch {}"
    )
    page.goto(srv.url)
    page.wait_for_selector("#hub [data-land]")
    page.check("#hub [data-land]")
    page.goto(srv.url)  # 不带 #m=hub 重新打开
    page.wait_for_selector("#ask")
    page.wait_for_timeout(300)
    assert page.is_visible("#main") and page.is_hidden("#hub")


def test_skin_toggle_switches_and_is_remembered(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#skin")
    assert page.evaluate("document.documentElement.dataset.skin") == "ark"
    page.click("#skin")
    assert page.evaluate("document.documentElement.dataset.skin") == "classic"
    page.reload()
    page.wait_for_selector("#skin")
    assert page.evaluate("document.documentElement.dataset.skin") == "classic"
