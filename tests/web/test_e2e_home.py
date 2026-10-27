"""前端：公寓楼入口（基建式剖面图 / 布局图）、Alt+数字切换入口、#m=home 刷新后回到原入口。"""

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
    page.emulate_media(reduced_motion="reduce")  # 白天住户一直在走动（wander），不停下就点不中
    page.locator("#home .tenant:not(.sleeper), #home .sleeper rect").first.click(force=True)
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
    page.click("#home .k-meeting .tenant.act-work", force=True)
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


def test_office_meeting_attendees_are_drawn_in_the_meeting_room(serve, page):
    """办公室模式开会时：经理和参会的员工在会议室，没参会的员工在办公室。"""
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    sid = repo.create_session("q", seed=1, anonymous=False, workflow="office")
    members = {"甲": "b2", "乙": "b3"}
    repo.create_table(
        sid,
        0,
        plan=None,
        pipeline=["intake", "handle", "check", "reveal"],
        members=members,
        coordinator="f1",
        escalate_to=None,
        estimate={},
        status="approved",
    )
    repo.add_seats(sid, 0, members, "f1")
    repo.save_output(
        sid,
        table_no=0,
        step="intake",
        kind="office_intake",
        content={"mode": "meeting", "attendees": ["甲"]},
    )
    repo.set_status(sid, "running")
    page.goto(srv.url + "#m=home")
    page.wait_for_selector("#home .k-meeting .tenant.act-work")
    assert page.locator("#home .k-meeting .tenant.act-work").count() == 2  # 经理 + 甲
    assert page.locator("#home .k-office .tenant.act-work").count() == 1  # 乙


def test_base_style_resource_bar_slots_zoom_and_map(serve, page):
    """基建式公寓楼：资源栏、空房暗槽、点房间放大、俯视布局图（刷新后记住）、进驻总览收起。"""
    srv = serve(confirm_threshold_usd=100.0)
    page.emulate_media(reduced_motion="reduce")
    page.goto(srv.url + "/#m=home")
    page.wait_for_selector("#home .tenant")
    n = len({m.vendor for m in srv.env.config.models.models if m.seat and m.tier})
    # 资源栏：在岗人数 / 住户数；有预算时显示今日或本月花费
    assert "".join(page.inner_text("#home .rchip.c3").split()) == f"在岗0/{n}"
    assert page.locator("#home .rchip.c1, #home .rchip.c2").count() >= 1
    # 宿舍层不满的补暗色空房，楼层对齐
    sizes = [len(range(n)[i : i + 3]) for i in range(0, n, 3)]
    assert page.locator("#home .slot").count() == sum(max(sizes) - s for s in sizes)
    # 放大按钮：大场景 + 在场的人（没人时一行「现在没有人」）；Esc 关闭
    here = page.locator("#home .bldg .room.k-office .tenant").count()
    page.click("#home .room.k-office .zbtn")
    page.wait_for_selector("#home .zoom .zstage .room.k-office.big")
    assert "办公室" in page.inner_text("#home .zbox header")
    assert page.locator("#home .zlist li").count() == max(here, 1)
    page.keyboard.press("Escape")
    page.wait_for_selector("#home .zoom", state="detached")
    # 点房间空白处也能放大；点遮罩关闭
    box = page.locator("#home .room.k-gym").bounding_box()
    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + 8)
    page.wait_for_selector("#home .zoom .room.k-gym")
    z = page.locator("#home .zoom").bounding_box()
    page.mouse.click(z["x"] + 6, z["y"] + z["height"] / 2)  # 遮罩左边缘（角上是圆角，点不到）
    page.wait_for_selector("#home .zoom", state="detached")
    # 布局图：每个房间一块牌子；刷新后还是布局图；点牌子放大
    page.click("#home [data-view='map']")
    page.wait_for_selector("#home .map .tile")
    assert page.locator("#home .bldg").count() == 0
    rooms = page.locator("#home .tile").count()
    page.reload()
    page.wait_for_selector("#home .map .tile")
    assert page.locator("#home .tile").count() == rooms
    page.click("#home .tile.k-canteen")
    page.wait_for_selector("#home .zoom .room.k-canteen")
    page.keyboard.press("Escape")
    page.click("#home [data-view='section']")
    page.wait_for_selector("#home .bldg .tenant")
    # 进驻总览可以收起；点住户时自动打开并显示卡片
    page.click("#home [data-ovw]")
    page.wait_for_selector("#home-side", state="hidden")
    page.locator("#home .tenant:not(.sleeper), #home .sleeper rect").first.click(force=True)
    page.wait_for_selector("#resident-card")
