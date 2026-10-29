"""前端：办公室模式是默认方式；显示经理分诊、员工办理、经理过目和交付。"""

from __future__ import annotations

import re

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
    # 空闲时的用词也是经理 / 员工
    assert page.inner_text(".panel .phd h2") == "经理面板"
    legend = page.inner_text("#legend")
    assert "经理：分诊派活" in legend and "组员" not in legend
    assert "经理怎么安排" in page.inner_text("#pbody")
    ask(page, MEDIUM)
    wait_done(page)
    chat = page.inner_text("#chat")
    assert "分诊派活" in chat and "派给" in chat and "单人办" in chat
    assert "预算单：单人办预计 $" in chat
    # 办公室模式的称呼是经理、员工，不说统筹、组员
    assert "经理 + 3 位在岗员工" in chat and "（经理）" in chat
    assert not re.search(r"\d 位组员|统筹|讨论完成", chat) and "办完了" in chat
    assert "3 位员工" in page.inner_text(".plate")
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
    assert "开会 / 项目分工要不要照办" in text and "超过开会的工资上限" in text
    page.click(".card:not(.resolved) .opt[data-opt='single']")
    wait_done(page)
    chat = page.inner_text("#chat")
    assert "不开会，改为单人办" in chat and "经理过目" in chat
    assert "经理已过目" in page.inner_text(".final")


def test_office_project_shows_team_table_and_review(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    srv.env.office_mode = "project"
    srv.env.office_review = "revise"
    _office_page(srv, page)
    ask(page, MEDIUM)
    wait_done(page)
    chat = page.inner_text("#chat")
    assert "决定项目分工" in chat and "项目组" in chat
    assert "复核经理" in chat and "退回修改一次" in chat
    assert "统筹" not in chat  # 办公室模式：项目组里统筹就是经理
    # 流程面板：第 1 桌的办理、过目交给项目组（跳过），不显示成完成
    flow = page.inner_text("#pbody")
    assert "交给项目组" in flow and "跳过" in flow
    finals = (
        page.inner_text("#chat .final:last-of-type")
        if False
        else page.locator(".final").all_inner_texts()
    )
    assert any("按验收意见修改" in f and "修改后的项目成果" in f for f in finals)
    # 「上桌」页：复核经理不在桌上，单独列出它的调用
    page.click('#tabs button[data-t="cast"]')
    cast = page.inner_text("#pbody")
    sid = srv.env.rt.repo.list_sessions()[0].id
    calls = srv.env.rt.repo.session_view(sid).calls
    [reviewer] = {c.model_id for c in calls if c.role == "reviewer"}
    assert f"复核经理 · {reviewer}" in cast and "复核经理验收" in cast and "项目组 · " in cast


def test_staff_panel_shows_performance_and_two_step_reset(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _office_page(srv, page)
    ask(page, MEDIUM)
    wait_done(page)
    page.click('#tabs button[data-t="office"]')
    perf = page.wait_for_selector("#office-perf")
    assert "单人办 1 件" in perf.inner_text() and "通过 100%" in perf.inner_text()
    page.click("#perf-reset")
    page.wait_for_selector("#perf-reset:has-text('再点一次确认清零')")
    assert page.query_selector("#office-perf")  # 第一次点只是确认，还没清零
    page.click("#perf-reset")
    page.wait_for_selector("#pbody :text('还没有记录')")
    assert not page.query_selector("#office-perf")


def test_estimate_lists_meeting_and_project_costs(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    _office_page(srv, page)
    page.fill("#ask", MEDIUM)
    page.wait_for_selector("#estimate #est-groups")
    text = page.inner_text("#estimate #est-groups")
    assert "以上按单人办估" in text and "开会（2～3 人）约 $" in text and "项目分工（2～" in text


def test_live_progress_only_highlights_the_assigned_staff(serve, page):
    """办理时只有派到的员工在干活，其他在岗员工不亮。"""
    srv = serve(delay=1.5, confirm_threshold_usd=100.0)
    _office_page(srv, page)
    ask(page, MEDIUM)
    page.wait_for_function(
        "document.querySelector('#plate-step')?.textContent === '办理'", timeout=20000
    )
    page.wait_for_selector("#stage .seat.speaking")
    assert page.locator("#stage .seat.speaking").count() == 1
    wait_done(page)


def test_model_directory_filters_and_offers_disabled_snippet(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click('#tabs button[data-t="models"]')
    # 第一次打开：没查过 → 自动拉取一次
    page.wait_for_selector("#mdir-list li[data-route='zz/brand-new-chat']")
    assert srv.directory_calls == 1
    body = page.inner_text("#pbody")
    assert "不会上桌" in body and "文字对话" in body and "绘图 1" in body and "向量 1" in body
    first = page.locator("#mdir-list li").first
    assert first.get_attribute("data-route") == "zz/brand-new-chat"  # 最新的在前
    assert "已在圆桌" in page.inner_text("#mdir-list")
    # 标签筛选：超长上下文只剩新模型
    page.click("#mdir-tag [data-g='huge_context']")
    assert page.locator("#mdir-list li").count() == 1
    # 配置片段默认停用
    page.click("#mdir-list li[data-route='zz/brand-new-chat'] summary")
    snippet = page.inner_text("#mdir-list li[data-route='zz/brand-new-chat'] pre")
    assert "enabled: false" in snippet and "model: zz/brand-new-chat" in snippet
    # 搜索时输入框不丢焦点
    page.click("#mdir-tag [data-g='huge_context']")
    page.fill("#mdir-q", "brand")
    assert page.evaluate("document.activeElement.id") == "mdir-q"
    assert page.locator("#mdir-list li").count() == 1
    # 换类别（搜索词保留，先清空）
    page.fill("#mdir-q", "")
    page.click("#mdir-cat [data-c='image']")
    page.wait_for_selector("#mdir-list li[data-route='zz/painter']")
    assert "每张输出图 $0.02" in page.inner_text("#mdir-list")
    page.click("#mdir-refresh")
    page.wait_for_function("() => !document.querySelector('#mdir-refresh').disabled")
    assert srv.directory_calls == 2
