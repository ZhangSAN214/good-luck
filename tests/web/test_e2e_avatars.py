"""成员头像：匿名会话揭晓前页面上没有任何头像图片或素材地址；匿名关闭时显示头像。"""

from __future__ import annotations

from ..core.members_samples import write_assets
from ..core.orchestrator.conftest import MEDIUM
from ..core.routing.conftest import app_config
from .test_e2e import ask, wait_done

ART_URL = "/api/members/files/"


def assets(tmp_path):
    """每个 vendor 一个成员（vendor 从测试配置里取，不写死）。"""
    vendors = sorted({m.vendor for m in app_config().models.models})
    extra = [
        {
            "id": f"m{i}",
            "name": f"成员{i}",
            "vendor": v,
            "nickname": f"昵称{i}",
            "theme_color": "#aa3355",
            "order": i,
            "images": {"avatar_circle": {"128": "m1/c128.png"}},
        }
        for i, v in enumerate(vendors[1:], start=2)
    ]
    return write_assets(tmp_path / "assets", vendors[0], extra=extra)


def avatar_imgs(page) -> int:
    return page.locator(".av img").count()


def test_anonymous_session_has_no_avatars(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0, members_dir=assets(tmp_path))
    page.goto(srv.url)
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    for tab in ("flow", "reviews", "cast"):
        page.click(f'#tabs button[data-t="{tab}"]')
        assert avatar_imgs(page) == 0
    assert page.locator(f"img[src*='{ART_URL}']").count() == 0
    assert ART_URL not in page.content()

    # 揭晓后才显示头像
    page.click("#reveal")
    page.wait_for_selector("#reveal:has-text('已揭晓')")
    page.wait_for_selector("#stage .seat .av img")


def test_not_anonymous_shows_avatars(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0, members_dir=assets(tmp_path))
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)
    page.wait_for_selector("#stage .seat .av img")
    img = page.locator("#chat .msg .av img").first
    assert img.get_attribute("src").startswith(ART_URL)
    assert img.get_attribute("loading") == "lazy"
    assert img.get_attribute("alt").startswith(("成员一", "昵称"))
    # 图片真的加载出来了（1×1 png）
    page.wait_for_function(
        "() => [...document.querySelectorAll('#stage .av img')]"
        ".every(i => i.complete && i.naturalWidth > 0)"
    )


def test_broken_avatar_falls_back_to_letter(serve, page, tmp_path):
    root = assets(tmp_path)
    for f in (root / "m1").iterdir():
        f.unlink()
    (root / "m1" / "c128.png").write_bytes(b"not an image")  # 存在但无法显示
    srv = serve(confirm_threshold_usd=100.0, members_dir=root)
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)
    # 图片移除、单字恢复；之后的重绘也不再使用这个地址
    page.wait_for_function(
        "() => { const avs = [...document.querySelectorAll('#stage .seat .av')];"
        " return avs.length > 0"
        " && avs.every(a => !a.querySelector('img') && a.textContent.trim()); }"
    )


def test_no_assets_keeps_letters(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)
    assert avatar_imgs(page) == 0
