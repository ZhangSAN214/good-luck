"""前端：讨论进行中补充意见——输入框只在进行中出现，补充显示在过程区，并标出是否已带给大家。"""

from __future__ import annotations

from ..core.orchestrator.conftest import MEDIUM
from .test_e2e import ask, leaks, wait_done


def test_note_box_appears_while_running_and_note_is_shown(serve, page):
    srv = serve(delay=0.5, confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#tier button[data-v='budget'][aria-pressed='true']")
    assert page.locator("#noteform").is_hidden()
    page.check("#anonymous")
    ask(page, MEDIUM)
    page.wait_for_selector("#noteform", state="visible", timeout=10000)
    page.fill("#note", "请重点检查端点值")
    page.click("#notesend")
    page.wait_for_selector(".msg.me:has-text('中途补充')", timeout=10000)
    assert page.input_value("#note") == ""
    wait_done(page)
    page.wait_for_selector(".msg.me:has-text('已带给大家')", timeout=10000)
    assert page.locator("#noteform").is_hidden()  # 结束后不再接受补充
    assert leaks(srv.anonymous_terms(), page.inner_text("#chat")) == []
