"""前端：平板（iPad / 安卓平板横竖屏、触屏）上的可用性，以及电脑上的「平板」面板。"""

from __future__ import annotations

import datetime as dt

import pytest

from .conftest import quiet_boot

AFTERNOON = dt.datetime(2026, 10, 27, 15, 0)
TABLETS = {
    "iPad 竖屏": (810, 1080),
    "iPad 横屏": (1080, 810),
    "iPad mini 竖屏": (744, 1133),
    # 用户的设备：OPPO Pad 4 Pro（13.2 英寸 3392×2400）横竖屏、一加 13、iPhone 13 Pro Max
    "OPPO Pad 4 Pro 横屏": (1280, 905),
    "OPPO Pad 4 Pro 竖屏": (905, 1280),
    "一加 13": (412, 915),
    "iPhone 13 Pro Max": (428, 926),
}


def tablet(browser, size):
    ctx = browser.new_context(
        viewport={"width": size[0], "height": size[1]}, has_touch=True, is_mobile=True
    )
    quiet_boot(ctx)
    pg = ctx.new_page()
    pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
    pg.route("https://fonts.gstatic.com/**", lambda r: r.abort())
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    return ctx, pg, errors


@pytest.mark.parametrize("name", list(TABLETS))
def test_tablet_pages_fit_the_screen(serve, browser, name):
    srv = serve(confirm_threshold_usd=100.0)
    ctx, pg, errors = tablet(browser, TABLETS[name])
    try:
        pg.goto(srv.url)
        for m in ("hub", "work", "home", "chat"):
            pg.tap(f"#entries button[data-m={m}]")
            pg.wait_for_timeout(300)
            width = pg.evaluate("[document.documentElement.scrollWidth, innerWidth]")
            assert width[0] <= width[1], (name, m, width)
        # 群聊的输入框在第一屏里（不用往下滚才能打字）
        box = pg.locator("#gchat textarea").bounding_box()
        assert box and box["y"] + box["height"] <= TABLETS[name][1]
        # 触屏上输入框字号不小于 16px（iPad / iPhone 的 Safari 才不会自动放大页面）
        font = "e => parseFloat(getComputedStyle(e).fontSize)"
        size = pg.eval_on_selector("#gchat textarea", font)
        assert size >= 16
    finally:
        ctx.close()
    assert errors == []


def test_landscape_hub_top_bar_does_not_cover_cards(serve, browser):
    srv = serve(confirm_threshold_usd=100.0)
    ctx, pg, errors = tablet(browser, (1080, 810))
    try:
        pg.add_init_script("try { localStorage.setItem('roundtable.landing', 'hub') } catch {}")
        pg.goto(srv.url)
        pg.wait_for_selector("#hub .hb-hero")
        # 等时钟填进顶栏再量（两个时区时顶栏约 53px 高；没填之前量会漏掉重叠）
        pg.wait_for_selector("#hub .hb-top .ck-face")
        # 卡片有进场动画（位置和大小在变）：等动画放完，量的是最后停下来的位置
        pg.wait_for_function(
            "() => document.querySelector('#hub .hb-col').getAnimations({subtree: true})"
            ".every(a => a.playState !== 'running')"
        )
        top = pg.locator("#hub .hb-top").bounding_box()
        hero = pg.locator("#hub .hb-hero").bounding_box()
        assert top["y"] + top["height"] <= hero["y"] + 1
        # 小卡的标题不再一个字一行
        heights = pg.eval_on_selector_all(
            "#hub .hb-row3 .hb-zh", "els => els.map(e => e.getBoundingClientRect().height)"
        )
        assert heights and max(heights) < 40
    finally:
        ctx.close()
    assert errors == []


def test_portrait_pad_uses_full_width(serve, browser):
    """OPPO Pad 竖屏：首页卡片占满宽度（不一字一行）；办公室模式按钮不折行、输入框在第一屏。"""
    srv = serve(confirm_threshold_usd=100.0)
    w, h = TABLETS["OPPO Pad 4 Pro 竖屏"]
    ctx, pg, errors = tablet(browser, (w, h))
    try:
        pg.add_init_script("try { localStorage.setItem('roundtable.landing', 'hub') } catch {}")
        pg.goto(srv.url)
        pg.wait_for_selector("#hub .hb-hero")
        hero = pg.locator("#hub .hb-hero").bounding_box()
        assert hero["width"] > w * 0.8
        heights = pg.eval_on_selector_all(
            "#hub .hb-row3 .hb-zh, #hub .hb-row2 .hb-zh",
            "els => els.map(e => e.getBoundingClientRect().height)",
        )
        assert heights and max(heights) < 40
        pg.tap("#entries button[data-m=work]")
        pg.wait_for_timeout(300)
        rows = pg.eval_on_selector_all(
            "#workflow button, #tier button",
            "els => els.map(e => e.getBoundingClientRect().height)",
        )
        assert rows and max(rows) < 40
        box = pg.locator("#composer textarea").first.bounding_box()
        assert box and box["y"] + box["height"] <= h
    finally:
        ctx.close()
    assert errors == []


def test_notch_safe_area():
    """iPhone 添加到主屏幕后全屏（viewport-fit=cover + 半透明状态栏）：内容要让开刘海和底部横条。"""
    from pathlib import Path

    web = Path(__file__).resolve().parents[2] / "web"
    assert "viewport-fit=cover" in (web / "index.html").read_text(encoding="utf-8")
    css = (web / "css" / "touch.css").read_text(encoding="utf-8")
    for side in ("top", "bottom", "left", "right"):
        assert f"env(safe-area-inset-{side})" in css


def touch_drag(pg, src, dst):
    """用真正的触摸事件（不是鼠标）拖动：按下、慢慢移动、松开。"""
    cdp = pg.context.new_cdp_session(pg)
    a = pg.locator(src).first.bounding_box()
    b = pg.locator(dst).first.bounding_box()
    x0, y0 = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    x1, y1 = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2

    def touch(kind, x, y):
        points = [] if kind == "touchEnd" else [{"x": x, "y": y, "id": 1}]
        cdp.send("Input.dispatchTouchEvent", {"type": kind, "touchPoints": points})

    touch("touchStart", x0, y0)
    for i in range(1, 13):
        t = i / 12
        touch("touchMove", x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
    touch("touchEnd", x1, y1)


def test_drag_resident_with_finger(serve, browser):
    srv = serve(confirm_threshold_usd=100.0)
    ctx, pg, errors = tablet(browser, (1080, 810))
    try:
        pg.clock.install(time=AFTERNOON)
        pg.emulate_media(reduced_motion="reduce")
        pg.goto(srv.url + "/#m=home")
        pg.wait_for_selector("#home .bldg .tenant:not(.sleeper)")
        rid = pg.locator(
            "#home .bldg .room:not(.k-canteen) .tenant:not(.sleeper)"
        ).first.get_attribute("data-r")
        touch_drag(pg, f"#home .bldg .tenant[data-r='{rid}']", "#home .bldg .room.k-canteen")
        pg.locator(f"#home .bldg .k-canteen .tenant[data-r='{rid}']").wait_for()
        assert pg.locator(".drag-ghost").count() == 0
    finally:
        ctx.close()
    assert errors == []


def test_pc_lan_panel_sets_password(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#lanbtn")
    page.wait_for_selector("#landlg[open] .lan-state.off")
    assert "start_lan.bat" in page.inner_text("#landlg")
    cmds = page.locator("#landlg .lan-cmd").all_inner_texts()
    assert "New-NetFirewallRule" in cmds[0]
    # 离家用的 Tailscale：只放行 100.64.0.0/10
    assert "Tailscale" in page.inner_text("#landlg")
    assert "-RemoteAddress 100.64.0.0/10" in cmds[1]
    page.fill("#landlg input[name=pw]", "home-" + "pass")
    page.click("#landlg .lan-pw button")
    page.wait_for_function(
        "document.querySelector('#landlg .lan-msg').textContent.includes('已保存')"
    )
    assert page.locator("#landlg .lan-pw button").inner_text() == "修改口令"


def test_manifest_and_login_page(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    assert page.get_attribute("link[rel=manifest]", "href") == "manifest.webmanifest"
    manifest = page.evaluate("fetch('/manifest.webmanifest').then(r => r.json())")
    assert manifest["display"] == "standalone" and len(manifest["icons"]) == 2
    page.goto(srv.url + "/login.html?expired=1")
    assert "过期" in page.inner_text("#msg")
    page.fill("#pw", "whatever-1")
    page.click("#go")
    # 本机不需要登录：这里服务没对局域网开放、也没设口令，登录被拒并说明原因
    page.wait_for_function("document.querySelector('#msg').textContent.length > 0")
