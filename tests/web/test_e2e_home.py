"""前端：公寓楼入口（像素剖面图）、Alt+数字切换入口、#m=home 刷新后回到原入口。"""

from __future__ import annotations


def test_apartment_entry_and_residents(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    secret = repo.create_session("q", seed=1, anonymous=True)
    repo.add_seats(secret, 0, {"甲": "b1"})
    repo.set_status(secret, "running")
    page.goto(srv.url)
    page.click("#entries button[data-m='home']")
    page.wait_for_selector("#home .tenant")
    assert page.url.endswith("#m=home")
    assert page.is_hidden("#main") and page.is_hidden("#workctrls")
    vendors = {m.vendor for m in srv.env.config.models.models if m.seat and m.tier}
    assert page.locator("#home .tenant").count() == len(vendors)
    # 匿名会议只挂牌，不说是谁
    assert "匿名会议进行中 ×1" in page.inner_text("#home .k-meeting")
    assert page.locator("#home .tenant.act-work").count() == 0
    # 点一位住户：右侧出现卡片；Esc 收起
    page.locator("#home .tenant").first.click()
    page.wait_for_selector("#resident-card")
    page.keyboard.press("Escape")
    page.wait_for_selector("#resident-card", state="detached")
    # 刷新后还在公寓楼；Alt+1 回到办公室
    page.reload()
    page.wait_for_selector("#home .tenant")
    page.keyboard.press("Alt+Digit1")
    page.wait_for_selector("#main", state="visible")
    assert page.is_hidden("#home") and "#m=" not in page.url
    page.keyboard.press("Alt+Digit2")
    page.wait_for_selector("#home .tenant")


def test_working_resident_links_to_the_session(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    sid = repo.create_session("在办的事", seed=1, anonymous=False)
    repo.add_seats(sid, 0, {"甲": "b2"})
    repo.set_status(sid, "running")
    page.goto(srv.url + "/#m=home")
    page.wait_for_selector("#home .k-meeting .tenant.act-work")
    page.click("#home .k-meeting .tenant.act-work")
    page.click("#resident-card [data-open]")
    page.wait_for_selector("#main", state="visible")
    page.wait_for_function(f"() => location.hash === '#s={sid}'")
