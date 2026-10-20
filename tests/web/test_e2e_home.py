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
    # 夜里住户睡在床上（SVG），点被子；白天点人
    page.locator("#home .tenant:not(.sleeper), #home .sleeper rect").first.click()
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


FAKE_3AM = (
    "const _D = Date; Date = class extends _D { constructor(...a) {"
    " if (a.length) super(...a); else { super(); this.setHours(3, 0, 0, 0); } } };"
    " Date.now = _D.now;"
)


def test_night_sleepers_lie_in_bed_and_nobody_leaves_the_building(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.add_init_script(FAKE_3AM)
    page.goto(srv.url + "/#m=home")
    page.wait_for_selector("#home .sleeper")
    vendors = {m.vendor for m in srv.env.config.models.models if m.seat and m.tier}
    # 凌晨三点：大家都在自己宿舍的床上，公共房间熄灯
    assert page.locator("#home .room.k-dorm .sleeper").count() == len(vendors)
    assert page.locator("#home .room.k-office.dark").count() == 1
    # 鼠标能点到床上的住户（住户层不挡住家具）；键盘也能选中
    page.locator("#home .sleeper rect").first.click()
    page.wait_for_selector("#resident-card")
    page.keyboard.press("Escape")
    page.focus("#home .sleeper")
    page.keyboard.press("Enter")
    page.wait_for_selector("#resident-card")
    # 网页小人只在办公室出现
    assert page.locator(".pet").count() == 0
    # 所有住户都在楼的范围内
    box = page.locator("#home .bldg").bounding_box()
    for el in page.locator("#home .tenant").all():
        b = el.bounding_box()
        assert box["x"] - 1 <= b["x"] and b["x"] + b["width"] <= box["x"] + box["width"] + 1
