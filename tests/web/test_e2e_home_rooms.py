"""公寓的场景图：房主有 room_day 就用图，公共房间有 rooms/ 的图也用图；图上标的家具能叫小人
过去用（换姿势、拿道具）；窗外天气可以手选。"""

from __future__ import annotations

import json

from ..core.members_samples import write_assets
from ..core.routing.conftest import app_config


def fake_hour(h: int) -> str:
    return (
        "const _D = Date; Date = class extends _D { constructor(...a) {"
        f" if (a.length) super(...a); else {{ super(); this.setHours({h}, 0, 0, 0); }} }} }};"
        " Date.now = _D.now;"
    )


def assets(tmp_path):
    vendor = sorted({m.vendor for m in app_config().models.models})[0]
    root = write_assets(tmp_path / "assets", vendor)
    data = json.loads((root / "members.json").read_text(encoding="utf-8"))
    data["members"][0]["room"] = {
        "bed": {"x": 10, "y": 54},
        "hots": [
            {
                "id": "chair",
                "name": "扶手椅",
                "act": "idle",
                "label": "窝在扶手椅里",
                "say": ["真舒服。"],
                "x": 80,
                "y": 50,
                "w": 16,
                "h": 36,
                "stand": 88,
                "lift": 8,
                "prop": "book",
            },
        ],
    }
    (root / "members.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    # 公共房间的场景图：休息室换成图，沙发能躺
    (root / "rooms").mkdir()
    (root / "rooms" / "lounge_day.webp").write_bytes((root / "m1" / "room_day.webp").read_bytes())
    sofa = {"id": "sofa", "name": "沙发", "act": "idle", "label": "躺在沙发上"}
    sofa |= {"x": 40, "y": 50, "w": 30, "h": 30, "pose": "lie", "lift": 10}
    rooms = {"lounge": {"day": "lounge_day.webp", "hots": [sofa]}}
    (root / "rooms" / "rooms.json").write_text(json.dumps(rooms, ensure_ascii=False), "utf-8")
    return root


def test_art_room_hotspot_and_window_weather(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0, members_dir=assets(tmp_path))
    page.add_init_script(fake_hour(22))  # 晚上十点：住户在自己宿舍
    page.emulate_media(reduced_motion="reduce")
    page.goto(srv.url + "/#m=home")
    art = page.locator("#home .room.k-dorm:has(svg.s3d image)")
    art.first.wait_for()
    assert page.locator("#home .room.k-lounge svg.s3d image").count() == 1
    assert page.locator("#home .room.k-office svg.s3d").count() == 0
    assert art.count() == 1
    assert "/api/members/files/m1/room_day.webp" in art.first.inner_html()
    art.first.locator(".zbtn").click()  # 不加 force：渲染中途换掉按钮时会自动重试
    page.wait_for_selector(".zstage [data-hot='chair']")
    assert page.locator(".zstage [data-hot='bed']").count() == 1
    page.locator(".zstage [data-hot='chair']").click(force=True)
    page.wait_for_function(
        "() => [...document.querySelectorAll('.zstage .tenant')]"
        ".some((t) => t.getAttribute('aria-label').includes('窝在扶手椅里')"
        " && t.style.getPropertyValue('--y') === '0.080'"
        " && t.style.getPropertyValue('--x') === '88.0%'"
        " && t.classList.contains('pose-sit') && t.querySelector('.prop.p-book'))"
    )
    page.locator(".zoom .zx").click()
    page.wait_for_selector(".zoom", state="detached")
    # 窗外天气：自动 → 晴 → 阴 → 雨，记在浏览器里；下雨时窗外有雨丝
    btn = page.locator("#home [data-weather]")
    assert "自动" in btn.inner_text()
    for _ in range(3):
        page.locator("#home [data-weather]").click()
    assert "雨" in page.locator("#home [data-weather]").inner_text()
    assert page.evaluate("localStorage.getItem('roundtable.apartment.weather')") == "rain"
    assert page.locator("#home .room.k-dorm svg.s3d .rain").count() == 1


def test_flat_room_furniture_pose_and_prop(serve, page):
    """自绘的公共房间：躺沙发换成躺姿，看电视手里有音符道具，挨着同一件家具的第二位往旁边让。"""
    srv = serve(confirm_threshold_usd=100.0)
    page.add_init_script(fake_hour(20))  # 晚上八点：休息室有人
    page.emulate_media(reduced_motion="reduce")
    page.goto(srv.url + "/#m=home")
    page.locator("#home .bstage .room.k-lounge .zbtn").click()
    page.wait_for_selector(".zstage [data-hot='sofa']")
    page.locator(".zstage [data-hot='sofa']").click(force=True)
    page.wait_for_selector(".zstage .tenant.using.pose-lie")
    page.locator(".zstage [data-hot='tv']").click(force=True)
    page.wait_for_selector(".zstage .tenant.using.pose-stand .prop.p-note")
