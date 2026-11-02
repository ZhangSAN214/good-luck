"""前端：Live2D 样机播放页（web/live2d/）。

测试环境不联网，Cubism Core（Live2D 官方地址）加载不到：页面要给出明确提示，不报未捕获的错；
本地的第三方库（pixi、pixi-live2d-display）要能加载。真模型只能在联网的电脑上看。"""

from __future__ import annotations

import pytest


def test_player_without_cubism_core_explains(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("https://cubism.live2d.com/**", lambda r: r.abort())
    page.goto(srv.url + "/live2d/index.html?model=models/none.model3.json")
    page.wait_for_function("document.querySelector('#status').textContent.includes('Cubism Core')")
    assert "联网" in page.inner_text("#status")
    assert page.evaluate("typeof PIXI") == "object"
    assert page.evaluate("PIXI.VERSION") == "6.5.10"
    # pixi-live2d-display 在没有 Core 时会提示，但页面自己的脚本不能抛未捕获的错
    assert not [e for e in errors if "player.js" in e or "driver.js" in e]


@pytest.mark.parametrize("width", [390, 1280])
def test_player_controls_fit(serve, page, width):
    srv = serve(confirm_threshold_usd=100.0)
    page.set_viewport_size({"width": width, "height": 800})
    page.route("https://cubism.live2d.com/**", lambda r: r.abort())
    page.goto(srv.url + "/live2d/index.html")
    page.wait_for_selector("#bar")
    overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
    assert not overflow
