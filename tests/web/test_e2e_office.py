"""前端：办公室模式是默认方式；显示经理分诊、员工办理、经理过目和交付。"""

from __future__ import annotations

from ..core.orchestrator.conftest import MEDIUM
from .test_e2e import ask, wait_done


def test_office_is_default_and_shows_assignment_check_and_delivery(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.evaluate("localStorage.removeItem('roundtable.workflow')")  # 没有记住的选择 → 默认
    page.add_init_script("localStorage.removeItem('roundtable.workflow')")
    page.reload()
    page.wait_for_selector("#workflow button[data-v='office'][aria-pressed='true']")
    assert "经理分诊后派一位员工办理" in page.inner_text("#mode-hint")
    assert "办公室模式先只出文字" in page.inner_text("#media")
    ask(page, MEDIUM)
    wait_done(page)
    chat = page.inner_text("#chat")
    assert "分诊派活" in chat and "派给" in chat and "单人办" in chat
    assert "办理" in chat and "过目" in chat and "通过" in chat
    final = page.inner_text(".final")
    assert "交付" in final and "经理已过目" in final and "最大值 2，最小值 -2" in final
    # 只有一位员工干活
    assert len(srv.env.models_called("办公室的员工")) == 1


def test_chosen_workflow_is_remembered(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#workflow button[data-v='discussion'][aria-pressed='true']")
    page.click("#workflow button[data-v='solo']")
    page.reload()
    page.wait_for_selector("#workflow button[data-v='solo'][aria-pressed='true']")
