"""开机动画「线条汇聚」：成员线稿生长 → 首页立绘那位上色 → 底色撤掉、落在首页立绘上。

成员图和立绘都用临时生成的色块图，不用真实素材。"""

from __future__ import annotations

import json

import pytest

from ..core.routing.conftest import app_config
from .conftest import wait_until
from .test_e2e_standee import LAND_HUB

PIL = pytest.importorskip("PIL.Image")


def members_dir(root):
    """三位成员：透明底上画一个带边的色块（线稿能提取出边）。"""
    vendors = sorted({m.vendor for m in app_config().models.models})
    members = []
    for i in range(3):
        d = root / f"m{i}"
        d.mkdir(parents=True, exist_ok=True)
        img = PIL.new("RGBA", (120, 160), (0, 0, 0, 0))
        img.paste((200, 90 + i * 40, 60, 255), (20, 20, 100, 150))
        img.paste((255, 255, 255, 255), (45, 50, 75, 80))
        img.save(d / "chibi.png")
        members.append(
            {
                "id": f"m{i}",
                "name": "立绘一" if i == 2 else f"成员{i}",
                "vendor": vendors[i % len(vendors)],
                "theme_color": "#3366cc",
                "order": i,
                "images": {"chibi_transparent": f"m{i}/chibi.png"},
            }
        )
    (root / "members.json").write_text(
        json.dumps({"version": 2, "members": members}, ensure_ascii=False), encoding="utf-8"
    )
    return root


def standee_file(root):
    root.mkdir(parents=True, exist_ok=True)
    img = PIL.new("RGBA", (200, 300), (0, 0, 0, 0))
    img.paste((230, 120, 60, 255), (40, 20, 160, 300))
    img.save(root / "a.png")
    variant = {"id": "regular", "label": "常规", "image": "a.png"}
    manifest = {"name": "立绘一", "variants": [variant]}
    (root / "standee.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root / "standee.json"


def boot_lines(page):
    page.add_init_script(
        "try { localStorage.setItem('roundtable.boot.mode', 'lines');"
        " localStorage.setItem('roundtable.boot.sound', 'off');"
        " localStorage.setItem('roundtable.boot.voice', 'off') } catch {}"
    )


def test_lines_land_on_the_hub_standee(serve, page, tmp_path):
    srv = serve(
        confirm_threshold_usd=100.0,
        members_dir=members_dir(tmp_path / "art"),
        standee_file=standee_file(tmp_path / "sd"),
    )
    page.add_init_script(LAND_HUB)
    boot_lines(page)
    page.emulate_media(reduced_motion="reduce")  # 2.2 倍速，约 4.6 秒放完
    page.goto(srv.url)
    page.wait_for_selector(".rt-boot iframe[src='boot/lines.html']")
    # 线条页画好第一帧后外层撤掉底色，最后露出的就是底下的首页
    page.wait_for_function("document.querySelector('.rt-boot').style.background === 'transparent'")
    # 落点 = 首页立绘在屏幕上的位置
    page.wait_for_function("window.roundtableLanding() && !window.roundtableLanding().pending")
    landing = page.evaluate(
        "() => { const L = window.roundtableLanding();"
        " return L && L.rect ? {...L.rect, name: L.name} : L; }"
    )
    box = page.locator("#hub .sd-cv").bounding_box()
    assert landing and landing.get("name") == "立绘一", landing
    assert box["x"] <= landing["x"] <= box["x"] + box["width"] * 0.2
    assert abs((landing["y"] + landing["h"]) - (box["y"] + box["height"])) < box["height"] * 0.1
    # 放完自己收起来，首页照常可用
    page.wait_for_selector(".rt-boot", state="detached", timeout=15000)
    assert page.is_visible("#hub")


def test_lines_without_hub_still_finishes(serve, page, tmp_path):
    """停在办公室（没有首页立绘）：最后一位就地上色，照样放完；Esc 随时跳过。"""
    srv = serve(confirm_threshold_usd=100.0, members_dir=members_dir(tmp_path / "art"))
    boot_lines(page)
    page.goto(srv.url)
    page.wait_for_selector(".rt-boot iframe[src='boot/lines.html']")
    page.wait_for_function("window.roundtableLanding() === null")
    page.wait_for_function("document.querySelector('.rt-boot').style.background === 'transparent'")
    page.keyboard.press("Escape")
    page.wait_for_selector(".rt-boot", state="detached")


def test_lines_with_no_art_skips_straight_to_home(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    boot_lines(page)
    page.goto(srv.url)
    page.wait_for_selector(".rt-boot", state="detached", timeout=8000)
    wait_until(lambda: page.is_visible("#main") or page.is_visible("#hub"))
