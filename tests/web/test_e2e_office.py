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


def test_talent_market_lists_changes_and_refreshes(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click('#tabs button[data-t="office"]')
    # 第一次打开：没查过 → 自动查一次
    page.wait_for_selector("#talent tr[data-kind='price']")
    talent = page.inner_text("#talent")
    assert "b1" in talent and "f5" in talent
    assert "不会自动生效" in page.inner_text("#pbody")
    assert srv.catalog_calls == 1
    page.click("#talent-refresh")
    page.wait_for_function("() => !document.querySelector('#talent-refresh').disabled")
    assert srv.catalog_calls == 2


def _office_page(srv, page):
    page.goto(srv.url)
    page.add_init_script("localStorage.setItem('roundtable.workflow', 'office')")
    page.reload()
    page.wait_for_selector("#workflow button[data-v='office'][aria-pressed='true']")


def test_office_meeting_shows_attendees_speeches_and_decision(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    srv.env.office_mode = "meeting"
    _office_page(srv, page)
    ask(page, MEDIUM)
    wait_done(page)
    chat = page.inner_text("#chat")
    assert "决定开会" in chat and "参会" in chat
    assert "开会" in chat and chat.count("发言") >= 2
    assert "经理过目" not in chat and "退回修改" not in chat
    final = page.inner_text(".final")
    assert "2 位员工开会" in final and "经理拍板" in final and "会议纪要" in final


def test_office_meeting_card_can_switch_to_single(serve, page):
    from roundtable.core.config.schema import OfficeTaskCaps

    from ..core.orchestrator.conftest import Env

    office = Env().config.roundtable.office.model_copy(
        update={"task_caps_usd": OfficeTaskCaps(single=100.0, meeting=0.000001)}
    )
    srv = serve(confirm_threshold_usd=100.0, rt_update={"office": office})
    srv.env.office_mode = "meeting"
    _office_page(srv, page)
    ask(page, MEDIUM)
    card = page.wait_for_selector(".card[data-kind='office']:not(.resolved)", timeout=15000)
    text = card.inner_text()
    assert "要不要开会" in text and "超过开会的工资上限" in text
    page.click(".card:not(.resolved) .opt[data-opt='single']")
    wait_done(page)
    chat = page.inner_text("#chat")
    assert "不开会，改为单人办" in chat and "经理过目" in chat
    assert "经理已过目" in page.inner_text(".final")
