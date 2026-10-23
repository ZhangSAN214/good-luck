"""键盘：Enter 提交、Shift+Enter 换行、输入法选字时的 Enter 不提交、全局快捷键。"""

from __future__ import annotations

from ..core.orchestrator.conftest import MEDIUM
from .test_e2e import wait_done


def sessions(srv) -> int:
    return len(srv.env.rt.repo.list_sessions())


def test_enter_submits_and_shift_enter_adds_a_line(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#ask")
    page.keyboard.type("第一行")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("第二行")
    assert page.input_value("#ask") == "第一行\n第二行" and sessions(srv) == 0
    # 输入法选字时按下的 Enter（isComposing）只确认候选词，不提交
    page.evaluate(
        """() => document.querySelector('#ask').dispatchEvent(new KeyboardEvent('keydown',
        {key: 'Enter', isComposing: true, bubbles: true, cancelable: true}))"""
    )
    page.wait_for_timeout(200)
    assert sessions(srv) == 0
    page.fill("#ask", MEDIUM)
    page.press("#ask", "Enter")
    wait_done(page)
    assert sessions(srv) == 1


def test_global_shortcuts(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#tabs button")
    page.click("#pstate")  # 焦点离开输入框
    page.keyboard.press("/")
    assert page.evaluate("document.activeElement.id") == "ask"
    page.keyboard.press("Escape")
    assert page.evaluate("document.activeElement.id") != "ask"
    page.keyboard.press("Control+k")
    assert page.evaluate("document.activeElement.id") == "ask"
    # 面板标签页用方向键切换
    page.focus("#tabs button[data-t='flow']")
    page.keyboard.press("ArrowRight")
    assert page.get_attribute("#tabs button[data-t='split']", "aria-selected") == "true"
    page.keyboard.press("End")
    assert page.get_attribute("#tabs button:last-child", "aria-selected") == "true"


def test_number_key_answers_the_pending_card(serve, page):
    srv = serve(difficulty="hard", confirm_threshold_usd=0.0001)
    page.goto(srv.url)
    page.fill("#ask", MEDIUM)
    page.press("#ask", "Enter")
    page.wait_for_selector(".card:not(.resolved)", timeout=15000)
    page.click("#pstate")
    page.keyboard.press("1")  # 第一个选项：继续
    page.wait_for_selector(".card.resolved .result:has-text('已选择')")
    wait_done(page)
