"""前端：终端首页的大立绘（伪 Live2D）。

抠底、对话框、点击台词、设置面板（大小 / 翻转 / 隐藏 / 恢复默认）、拖动。

立绘用临时生成的图（纯色底上一个色块），不用真实素材。"""

from __future__ import annotations

import json

import pytest

PIL = pytest.importorskip("PIL.Image")

LAND_HUB = (
    "try { if (!sessionStorage.getItem('hub-once')) { sessionStorage.setItem('hub-once', '1');"
    " localStorage.setItem('roundtable.landing', 'hub') } } catch {}"
)


def make_standee(root):
    root.mkdir(parents=True, exist_ok=True)
    img = PIL.new("RGB", (200, 300), (27, 35, 56))
    img.paste((230, 120, 60), (40, 20, 160, 300))  # 「人物」：碰到底边，像二游立绘那样被画框截断
    img.save(root / "a.png")
    PIL.new("RGB", (240, 240), (27, 35, 56)).save(root / "b.png")
    manifest = {
        "name": "立绘一",
        "variants": [
            {"id": "regular", "label": "常规", "image": "a.png", "head": [0.5, 0.15]},
            {"id": "special", "label": "特殊", "image": "b.png"},
        ],
        "lines": {"greet": ["你好呀。"], "tap": ["点到我了。"], "head": ["头发会乱的。"]},
    }
    (root / "standee.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root / "standee.json"


def stored(page):
    return page.evaluate("JSON.parse(localStorage.getItem('roundtable.hub.standee') || '{}')")


def test_standee_cutout_lines_and_tap(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0, standee_file=make_standee(tmp_path / "sd"))
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .sd-stage:not([hidden])")
    # 有立绘：首页提示改由她在对话框里说，原来那句藏起来
    assert page.is_hidden("#hub .hb-voice")
    page.wait_for_selector("#hub .sd-say:not([hidden])")
    assert page.inner_text("#hub .sd-name") == "立绘一"
    page.wait_for_function("document.querySelector('#hub .sd-text').textContent === '你好呀。'")
    # 纯色底被抠掉：画布角上透明，色块处不透明
    alpha = page.evaluate(
        """() => { const c = document.querySelector('#hub .sd-cv'); const x = c.getContext('2d');
        return [x.getImageData(c.width * 0.06, c.height * 0.3, 1, 1).data[3],
                x.getImageData(c.width / 2, c.height / 2, 1, 1).data[3]]; }"""
    )
    assert alpha[0] == 0 and alpha[1] > 200
    # 点身体：说点击台词；点透明处没反应
    box = page.locator("#hub .sd-cv").bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.06, box["y"] + box["height"] * 0.5)
    page.wait_for_timeout(200)
    assert "点到我了" not in page.inner_text("#hub .sd-say")
    page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.6)
    page.wait_for_function("document.querySelector('#hub .sd-text').textContent === '点到我了。'")
    page.wait_for_timeout(600)  # 两次点击之间有冷却
    page.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.12)
    page.wait_for_function("document.querySelector('#hub .sd-text').textContent === '头发会乱的。'")


def test_standee_settings_are_saved_and_can_be_reset(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0, standee_file=make_standee(tmp_path / "sd"))
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .sd-stage:not([hidden])")
    page.click("#hub [data-act='standee']")
    page.wait_for_selector("#hub .sd-panel:not([hidden])")
    assert page.get_attribute("#hub [data-act='standee']", "aria-expanded") == "true"
    assert page.is_visible("#hub .sd-grip")  # 编辑中：可以拖右下角缩放
    w0 = page.locator("#hub .sd-stage").bounding_box()["width"]
    page.fill("#hub [data-sd='size']", "150")
    page.dispatch_event("#hub [data-sd='size']", "input")
    assert page.locator("#hub .sd-stage").bounding_box()["width"] > w0 * 1.4
    page.check("#hub [data-sd='flip']")
    page.fill("#hub [data-sd='opacity']", "60")
    page.dispatch_event("#hub [data-sd='opacity']", "input")
    # 拖动立绘本身改变位置（先关掉吸附，免得吸到参考线上）
    page.uncheck("#hub [data-sd='snap']")
    box = page.locator("#hub .sd-cv").bounding_box()
    x0 = box["x"]
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 37, box["y"] + box["height"] / 2, steps=4)
    page.mouse.up()
    assert abs(page.locator("#hub .sd-cv").bounding_box()["x"] - (x0 + 37)) < 3
    saved = stored(page)["per"]["regular"]
    assert saved["flip"] is True and saved["opacity"] == 0.6 and saved["h"] > 1.5
    assert stored(page)["snap"] is False
    # 刷新后还在
    page.reload()
    page.wait_for_selector("#hub .sd-stage:not([hidden])")
    assert page.evaluate("document.querySelector('#hub .sd-stage').style.opacity") == "0.6"
    # 换成另一套，再隐藏：原来的提示回来
    page.click("#hub [data-act='standee']")
    page.click("#hub [data-sd-variant='special']")
    page.wait_for_selector("#hub [data-sd-variant='special'][aria-pressed='true']")
    assert stored(page)["variant"] == "special"
    page.uncheck("#hub [data-sd='show']")
    assert page.is_hidden("#hub .sd-stage") and page.is_visible("#hub .hb-voice")
    # 恢复默认：重新显示，这一套的位置大小回到默认
    page.click("#hub [data-sd='reset']")
    assert page.is_visible("#hub .sd-stage") and page.is_hidden("#hub .hb-voice")
    assert "special" not in stored(page).get("per", {})
    page.click("#hub [data-sd='done']")
    assert page.is_hidden("#hub .sd-panel")


def test_without_standee_the_panel_explains_why(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.add_init_script(LAND_HUB)
    page.goto(srv.url)
    page.wait_for_selector("#hub .hb-hero")
    assert page.is_hidden("#hub .sd-stage") and page.is_visible("#hub .hb-voice")
    page.click("#hub [data-act='standee']")
    page.wait_for_selector("#hub .sd-panel:not([hidden])")
    assert "还没有立绘" in page.inner_text("#hub .sd-panel")
    page.keyboard.press("Escape")
    assert page.is_hidden("#hub .sd-panel")
