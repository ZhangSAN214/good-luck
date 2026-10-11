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
    assert img.get_attribute("src") == f"{ART_URL}m1/chibi.png"
    assert img.get_attribute("alt") == "小一"
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

    # 轻点一下会跳；双击开始演示
    page.locator(f"{PET} .pet-img").click(force=True)
    page.wait_for_selector(f"{PET}[data-action='jump'][data-face='happy']")


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
    page.locator(f"{PET} .pet-img").click(force=True)  # 跳：用专门的帧，不叠加程序姿势
    page.wait_for_selector(f"{PET}.framed[data-action='jump'] .pet-img[src$='m1/jump.png']")
