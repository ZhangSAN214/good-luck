"""网页小人：显示、拖动贴边、位置保留、隐藏与叫回；匿名未揭晓时不出现；没有素材或存储出错时不报错。"""

from __future__ import annotations

import json

from ..core.members_samples import PNG_1PX
from ..core.orchestrator.conftest import MEDIUM
from .test_e2e import ask, wait_done
from .test_e2e_avatars import ART_URL, assets

PET = ".pet"
KEY = "roundtable.pet"


def wait_pet(page):
    page.wait_for_selector(f"{PET}[data-action] .pet-img")
    page.wait_for_function(
        "() => { const i = document.querySelector('.pet .pet-img');"
        " return i && i.complete && i.naturalWidth > 0; }"
    )


def box(page):
    return page.locator(PET).bounding_box()


def tap_body(page):
    b = box(page)
    page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] * 0.75)


def tap_head(page):
    b = box(page)
    page.mouse.click(b["x"] + b["width"] / 2, b["y"] + b["height"] * 0.2)


def drag(page, dx, dy):
    b = box(page)
    x, y = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + dx / 2, y + dy / 2, steps=4)
    page.wait_for_selector(f"{PET}[data-action='drag'].held")  # 被拖起：挣扎
    page.mouse.move(x + dx, y + dy, steps=4)
    page.mouse.up()


def test_pet_shows_chibi_and_can_be_dragged_and_snapped(serve, page, tmp_path):
    srv = serve(members_dir=assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    img = page.locator(f"{PET} .pet-img")
    # 素材带浅色底时会在浏览器里抠掉底色，换成抠好的图（blob:）
    src = img.get_attribute("src")
    assert src == f"{ART_URL}m1/chibi.png" or src.startswith("blob:")
    assert img.get_attribute("alt") == "成员一"
    assert page.locator(PET).count() == 1
    b = box(page)
    assert b["x"] > 1000 and b["y"] > 600  # 默认在右下角

    # 拖到靠近左边松手：贴到左边并落地
    drag(page, -(b["x"] - 20), -300)
    page.wait_for_selector(f"{PET}[data-edge='left']")
    assert page.get_attribute(PET, "data-action") in {"land", "idle"}
    assert page.eval_on_selector(PET, "e => e.style.left") == "0px"
    saved = json.loads(page.evaluate(f"localStorage.getItem('{KEY}')"))
    assert saved["x"] == 0 and saved["edge"] == "left"

    # 刷新后还在原处
    page.reload()
    wait_pet(page)
    assert page.eval_on_selector(PET, "e => e.style.left") == "0px"
    assert page.get_attribute(PET, "data-edge") == "left"

    # 轻点身体会被戳到：惊讶地跳一下
    tap_body(page)
    page.wait_for_selector(f"{PET}[data-action='jump'][data-face='surprised']")


def test_hide_and_call_back(serve, page, tmp_path):
    srv = serve(members_dir=assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    page.hover(PET)
    page.click(f"{PET} .pet-hide")
    assert page.locator(PET).count() == 0
    page.wait_for_selector(".pet-call")
    page.reload()
    page.wait_for_selector(".pet-call")  # 隐藏状态也会记住
    assert page.locator(PET).count() == 0
    page.click(".pet-call")
    wait_pet(page)
    assert page.locator(".pet-call").count() == 0


def test_anonymous_session_has_no_pet_until_revealed(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0, members_dir=assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator(PET).count() == 0 and page.locator(".pet-call").count() == 0
    assert ART_URL not in page.content()

    # 直接打开匿名会话的地址：从头到尾都不出现
    page.reload()
    wait_done(page)
    page.wait_for_timeout(300)
    assert page.locator(PET).count() == 0
    assert ART_URL not in page.content()

    page.click("#reveal")
    page.wait_for_selector("#reveal:has-text('已揭晓')")
    wait_pet(page)

    # 新题目（还没有会话）：小人在；再提交一个匿名题目，小人离开
    page.click("#new")
    wait_pet(page)
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    page.wait_for_function("() => !document.querySelector('.pet')")
    assert ART_URL not in page.content()


def test_no_assets_no_pet_and_no_errors(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator(PET).count() == 0 and page.locator(".pet-call").count() == 0


def test_broken_chibi_is_not_shown(serve, page, tmp_path):
    root = assets(tmp_path)
    (root / "m1" / "chibi.png").write_bytes(b"not an image")
    srv = serve(members_dir=root)
    page.goto(srv.url)
    page.wait_for_function("() => !document.querySelector('.pet')")
    page.wait_for_timeout(300)
    assert page.locator(PET).count() == 0


def test_storage_errors_do_not_break_the_page(serve, page, tmp_path):
    page.add_init_script(
        "Object.defineProperty(window, 'localStorage', { get() { throw new Error('denied'); } });"
    )
    srv = serve(members_dir=assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    drag(page, -200, -200)
    page.wait_for_selector(f"{PET}:not(.held)")
    page.hover(PET)
    page.click(f"{PET} .pet-hide")
    page.click(".pet-call")
    wait_pet(page)


def test_pet_json_frames_are_used(serve, page, tmp_path):
    root = assets(tmp_path)
    (root / "m1" / "idle").mkdir()
    for i in range(2):
        (root / "m1" / "idle" / f"{i}.png").write_bytes(PNG_1PX)
    (root / "m1" / "jump.png").write_bytes(PNG_1PX)
    (root / "m1" / "pet.json").write_text(
        json.dumps(
            {
                "fps": 10,
                "actions": {
                    "idle": {"frames": ["idle/0.png", "idle/1.png"], "loop": True},
                    "jump": {"frames": ["jump.png"]},
                },
            }
        ),
        encoding="utf-8",
    )
    srv = serve(members_dir=root)
    page.goto(srv.url)
    wait_pet(page)
    srcs = set()
    for _ in range(12):
        srcs.add(page.get_attribute(f"{PET} .pet-img", "src"))
        page.wait_for_timeout(60)
    assert {f"{ART_URL}m1/idle/0.png", f"{ART_URL}m1/idle/1.png"} <= srcs
    tap_body(page)  # 跳：用专门的帧，不叠加程序姿势
    page.wait_for_selector(f"{PET}.framed[data-action='jump'] .pet-img[src$='m1/jump.png']")


# --- R3：汇报与互动 ---------------------------------------------------------------------


def test_pet_reacts_to_discussion_and_bubble_jumps_to_discussion(serve, page, tmp_path):
    srv = serve(delay=0.4, confirm_threshold_usd=100.0, members_dir=assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    ask(page, MEDIUM)
    # 讨论中冒过进度气泡（可能很快就过去，所以记下来）
    page.wait_for_function("() => document.querySelector('.pet-say:not([hidden])')", timeout=15000)
    wait_done(page)
    page.wait_for_selector(f"{PET}[data-action='celebrate']")
    say = page.locator(f"{PET} .pet-say")
    assert "完成" in say.inner_text()
    # 点气泡：气泡收起，讨论区滚到最新
    say.click()
    assert say.is_hidden()


def test_head_pat_poke_and_angry(serve, page, tmp_path):
    srv = serve(members_dir=assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    tap_head(page)
    page.wait_for_selector(f"{PET}[data-face='shy']")
    page.wait_for_timeout(100)
    for _ in range(3):
        tap_body(page)
        page.wait_for_timeout(60)
    page.wait_for_selector(f"{PET}[data-face='angry']")
    assert "戳" in page.locator(f"{PET} .pet-say").inner_text()


def test_menu_who_status_quiet_and_switch_member(serve, page, tmp_path):
    srv = serve(members_dir=assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    page.hover(PET)
    page.click(f"{PET} .pet-more")
    page.click(".pet-menu [data-item='who']")
    assert "AI" in page.locator(f"{PET} .pet-say").inner_text()
    assert page.locator(".pet-menu").count() == 0  # 选完就关

    page.click(f"{PET} .pet-more")
    page.click(".pet-menu [data-item='status']")
    assert page.locator(f"{PET} .pet-say").inner_text() == "空闲"

    # 安静一会儿：之后不再冒进度气泡，菜单里变成「不用安静了」
    page.click(f"{PET} .pet-more")
    page.click(".pet-menu [data-item='quiet']")
    page.click(f"{PET} .pet-more")
    assert page.locator(".pet-menu [data-item='unquiet']").count() == 1
    page.keyboard.press("Escape")
    assert page.locator(".pet-menu").count() == 0


def test_no_bubbles_leak_for_anonymous_unrevealed_session(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0, members_dir=assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator(PET).count() == 0 and page.locator(".pet-say").count() == 0


# --- 特殊待机 / 彩蛋 / 摸头行礼 / 手机 ------------------------------------------------------


def special_assets(tmp_path, *, probability=0.0, after_s=5):
    root = assets(tmp_path)
    clips = {"idle": 2, "curtsy": 3, "letter": 2, "gift": 2}
    for name, n in clips.items():
        (root / "m1" / name).mkdir(exist_ok=True)
        for i in range(n):
            (root / "m1" / name / f"{i}.png").write_bytes(PNG_1PX)
    (root / "m1" / "pet.json").write_text(
        json.dumps(
            {
                "fps": 10,
                "actions": {
                    name: {
                        "frames": [f"{name}/{i}.png" for i in range(n)],
                        "loop": name in ("idle", "letter"),
                    }
                    for name, n in clips.items()
                },
                "special_idle": {"action": "letter", "after_s": after_s},
                "easter_eggs": [
                    {
                        "action": "gift",
                        "probability": probability,
                        "cooldown_s": 0,
                        "max_per_day": 5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_head_pat_curtsies_and_ignores_taps_while_it_plays(serve, page, tmp_path):
    srv = serve(members_dir=special_assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    tap_head(page)
    page.wait_for_selector(f"{PET}[data-action='curtsy']")
    tap_body(page)  # 行礼中：忽略
    assert page.get_attribute(PET, "data-action") == "curtsy"
    page.wait_for_selector(f"{PET}[data-action='idle']")
    tap_body(page)  # 播完后恢复响应
    page.wait_for_selector(f"{PET}[data-action='jump']")


def test_special_idle_writes_until_user_is_active_again(serve, page, tmp_path):
    srv = serve(members_dir=special_assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    page.wait_for_selector(f"{PET}[data-action='letter']", timeout=15000)
    page.wait_for_timeout(1500)
    assert page.get_attribute(PET, "data-action") == "letter"  # 不自己结束
    page.mouse.move(5, 5)
    page.mouse.move(9, 9)
    page.wait_for_selector(f"{PET}[data-action='idle']")


def test_return_from_special_idle_can_trigger_the_egg(serve, page, tmp_path):
    srv = serve(members_dir=special_assets(tmp_path, probability=1.0))
    page.goto(srv.url)
    wait_pet(page)
    page.wait_for_selector(f"{PET}[data-action='letter']", timeout=15000)
    page.mouse.move(5, 5)
    page.mouse.move(9, 9)
    page.wait_for_selector(f"{PET}[data-action='gift']")
    state = json.loads(page.evaluate("() => localStorage.getItem('roundtable.pet.egg')"))
    assert state["gift"]["count"] == 1


def test_phone_viewport_long_press_drags_and_swipe_does_not(serve, page, tmp_path):
    page.set_viewport_size({"width": 390, "height": 700})
    srv = serve(members_dir=special_assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)
    assert "phone" in page.get_attribute(PET, "class")
    start = box(page)
    touch = (
        "(a) => { const el = document.querySelector('.pet'); const r = el.getBoundingClientRect();"
        " const x = r.x + r.width / 2, y = r.y + r.height / 2;"
        " const ev = (t, dx) => el.dispatchEvent(new PointerEvent(t, {"
        " pointerType: 'touch', pointerId: 7, button: 0, bubbles: true,"
        " clientX: x + dx, clientY: y }));"
        " return new Promise((ok) => { ev('pointerdown', 0);"
        " setTimeout(() => { ev('pointermove', 60); ev('pointerup', 60); ok(); }, a); }); }"
    )
    page.evaluate(touch, 50)  # 没按够 300ms：算轻划，不拖
    assert box(page)["x"] == start["x"]
    page.evaluate(touch, 450)  # 长按之后再动：拖起来
    assert box(page)["x"] != start["x"]


def test_hidden_page_pauses_animation(serve, page, tmp_path):
    srv = serve(members_dir=special_assets(tmp_path))
    page.goto(srv.url)
    wait_pet(page)

    def sample(n=10):
        seen = set()
        for _ in range(n):
            seen.add(page.get_attribute(f"{PET} .pet-img", "src"))
            page.wait_for_timeout(70)
        return seen

    set_hidden = (
        "(h) => { Object.defineProperty(document, 'hidden', { value: h, configurable: true });"
        " document.dispatchEvent(new Event('visibilitychange')); }"
    )
    page.evaluate(set_hidden, True)
    page.wait_for_timeout(200)
    assert len(sample()) == 1
    page.evaluate(set_hidden, False)
    assert len(sample()) > 1
