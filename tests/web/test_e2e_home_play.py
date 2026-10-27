"""前端：公寓楼里的互动（拖小人换房间、点小人互动菜单、放大后点家具、几分钟后自己回去）。"""

from __future__ import annotations

import datetime as dt

AFTERNOON = dt.datetime(2026, 10, 27, 15, 0)  # 白天，大家都醒着


def _open(srv, page):
    page.clock.install(time=AFTERNOON)
    page.emulate_media(reduced_motion="reduce")  # 白天住户在走动，不停下就点不中
    page.goto(srv.url + "/#m=home")
    page.wait_for_selector("#home .bldg .tenant:not(.sleeper)")


def _drag(page, src, dst, dx=0.5):
    a = page.locator(src).first.bounding_box()
    b = page.locator(dst).first.bounding_box()
    page.mouse.move(a["x"] + a["width"] / 2, a["y"] + a["height"] / 2)
    page.mouse.down()
    page.mouse.move(a["x"] + a["width"] / 2 + 10, a["y"] + a["height"] / 2, steps=3)
    page.mouse.move(b["x"] + b["width"] * dx, b["y"] + b["height"] / 2, steps=8)
    page.mouse.up()


def _somebody(page, away_from):
    """不在 away_from 房间里、醒着的一位住户。"""
    return page.locator(
        f"#home .bldg .room:not(.k-{away_from}) .tenant:not(.sleeper)"
    ).first.get_attribute("data-r")


def test_drag_resident_to_another_room_then_send_back(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _open(srv, page)
    working = page.inner_text("#home .rchip.c3")
    rid = _somebody(page, "canteen")
    _drag(page, f"#home .bldg .tenant[data-r='{rid}']", "#home .bldg .room.k-canteen")
    # 落在食堂、吃东西、头顶冒一句话；只是临时摆放
    moved = page.locator(f"#home .bldg .k-canteen .tenant[data-r='{rid}']")
    moved.wait_for()
    assert "act-eat" in moved.get_attribute("class") and "placed" in moved.get_attribute("class")
    assert "在食堂吃点东西" in moved.get_attribute("aria-label")
    assert moved.locator(".say").count() == 1
    assert page.locator(".drag-ghost").count() == 0 and page.locator("#home .drop-on").count() == 0
    # 松手不算点击：没有打开互动菜单、没有放大房间
    assert page.locator("#home .tpop").count() == 0 and page.locator("#home .zoom").count() == 0
    # 在岗人数按真实作息，不受摆放影响
    assert page.inner_text("#home .rchip.c3") == working
    # 选中后卡片说明是临时摆放的，可以马上让 TA 回去
    moved.click()
    page.wait_for_selector("#resident-card .placed-note")
    page.click("#resident-card [data-back]")
    page.wait_for_selector(f"#home .bldg .k-canteen .tenant[data-r='{rid}']", state="detached")
    assert page.locator(f"#home .tenant[data-r='{rid}'].placed").count() == 0


def test_dragged_resident_walks_back_by_itself(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _open(srv, page)
    rid = _somebody(page, "gym")
    _drag(page, f"#home .bldg .tenant[data-r='{rid}']", "#home .bldg .room.k-gym")
    page.wait_for_selector(f"#home .bldg .k-gym .tenant[data-r='{rid}'].act-exercise")
    page.clock.run_for("03:05")
    page.wait_for_selector(f"#home .bldg .k-gym .tenant[data-r='{rid}'].placed", state="detached")


def test_visit_a_neighbour_dorm(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _open(srv, page)
    rid = _somebody(page, "dorm")
    dorms = page.locator("#home .bldg .room.k-dorm")
    own = None
    for i in range(dorms.count()):
        if rid not in (dorms.nth(i).get_attribute("data-room") or ""):
            own = dorms.nth(i).get_attribute("data-room")
            break
    _drag(page, f"#home .bldg .tenant[data-r='{rid}']", f"#home .bldg .room[data-room='{own}']")
    t = page.locator(f"#home .bldg .room[data-room='{own}'] .tenant[data-r='{rid}']")
    t.wait_for()
    label = t.get_attribute("aria-label")
    assert any(w in label for w in ("串门", "睡觉", "等 TA 回来"))


def test_click_resident_for_interaction_menu(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _open(srv, page)
    t = page.locator("#home .bldg .tenant:not(.sleeper)").first
    rid = t.get_attribute("data-r")
    t.click()
    page.wait_for_selector("#home .tpop", state="visible")
    assert page.locator("#home .tpop [data-dm]").get_attribute("data-dm") == rid
    page.click("#home .tpop [data-act='pat']")
    page.wait_for_selector(f"#home .tenant[data-r='{rid}'] .say")
    page.click("#home .tpop [data-act='status']")
    say = page.locator(f"#home .tenant[data-r='{rid}'] .say")
    say.filter(has_text="本月工资").wait_for()
    # 成员设定
    page.click("#home .tpop [data-setup]")
    page.wait_for_selector("#people[open]")
    page.keyboard.press("Escape")
    # 再点一次收起
    page.locator(f"#home .bldg .tenant[data-r='{rid}']").click()
    page.wait_for_selector("#home .tpop", state="detached")


def test_furniture_in_zoomed_room(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _open(srv, page)
    # 没人的休息室：点电视，电视自己冒一句
    page.click("#home .room.k-lounge .zbtn")
    page.wait_for_selector("#home .zoom .room.k-lounge")
    if page.locator("#home .zoom .tenant").count() == 0:
        page.click("#home .zoom [data-hot='tv']", force=True)
        page.wait_for_selector("#home .zoom .say.free")
    page.keyboard.press("Escape")
    # 拖一位进休息室，再放大点电视：TA 去看电视
    rid = _somebody(page, "lounge")
    _drag(page, f"#home .bldg .tenant[data-r='{rid}']", "#home .bldg .room.k-lounge", dx=0.3)
    page.wait_for_selector(f"#home .bldg .k-lounge .tenant[data-r='{rid}']")
    page.click("#home .room.k-lounge .zbtn")
    page.wait_for_selector("#home .zoom .room.k-lounge [data-hot='sofa']")
    page.locator(f"#home .zoom .tenant[data-r='{rid}']").click()
    page.click("#home .zoom [data-hot='tv']", force=True)
    t = page.locator(f"#home .zoom .tenant[data-r='{rid}']")
    page.locator(f"#home .zoom .tenant[data-r='{rid}'][aria-label*='在休息室看电视']").wait_for()
    assert "act-game" in t.get_attribute("class")
    # 放大时拖动只在放大的房间里挪位置
    before = t.bounding_box()["x"]
    _drag(page, f"#home .zoom .tenant[data-r='{rid}']", "#home .zoom .room.k-lounge", dx=0.85)
    sel = f"#home .zoom .tenant[data-r='{rid}']"
    page.wait_for_function(
        f'() => document.querySelector("{sel}").getBoundingClientRect().x > {before} + 50'
    )
