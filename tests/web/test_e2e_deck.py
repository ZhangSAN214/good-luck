"""办公室左栏：思考时圆桌中央循环花切，结束后翻开结果牌再露出桌面文字；标题旁显示当前步骤。"""

from __future__ import annotations

from ..core.orchestrator.conftest import MEDIUM
from .test_e2e import ask, wait_done


def test_deck_loops_while_thinking_and_reveals_after(serve, page):
    srv = serve(delay=0.8, confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#legend .row")
    assert page.inner_text("#st-state") == "空闲"
    assert page.locator("#stage .deck:not(.on)").count() == 1  # 空闲时不显示

    ask(page, MEDIUM)
    page.wait_for_selector("#stage .deck.on", timeout=15000)
    assert "shuffling" in page.get_attribute("#stage", "class")
    page.wait_for_function(
        "document.querySelector('#st-state').textContent.includes('桌 ·')", timeout=15000
    )
    # 6 张牌面 + 1 张结果牌，都是自绘线条画
    assert page.locator("#stage .deck .dk-face").count() == 7
    # 座位图随事件重画，但动画节点不换（循环不会从头开始）
    page.evaluate("document.querySelector('#stage .deck').dataset.mark = '1'")
    page.wait_for_function(
        "document.querySelector('#plate-step')?.textContent === '互评'", timeout=15000
    )
    assert page.get_attribute("#stage .deck", "data-mark") == "1"
    assert page.locator("#stage .deck").count() == 1

    wait_done(page)
    page.wait_for_selector("#stage .deck.reveal", timeout=5000)
    # 揭牌结束后整副牌隐藏
    page.wait_for_function(
        "document.querySelector('#stage .deck').className === 'deck'", timeout=6000
    )
    assert "shuffling" not in page.get_attribute("#stage", "class")
    assert page.inner_text("#st-state") == "已完成"
    assert page.inner_text("#plate-step") == "已完成"
