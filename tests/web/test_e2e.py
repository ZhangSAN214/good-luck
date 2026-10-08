"""前端端到端：三种模式各跑一次；超门槛出现确认卡片；揭晓前页面不显示模型和渠道。"""

from __future__ import annotations

import re

from ..core.orchestrator.conftest import MEDIUM, SHORT

DONE = "讨论完成"


def leaks(terms: set[str], text: str) -> list[str]:
    return sorted(
        t for t in terms if re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", text)
    )


def anonymous_text(page) -> str:
    """揭晓前所有与会话相关的区域：圆桌、过程区、面板（流程 / 互评 / 名册）、页头。"""
    parts = [page.inner_text("header"), page.inner_text("#stage"), page.inner_text("#chat")]
    for tab in ("flow", "reviews", "cast"):
        page.click(f'#tabs button[data-t="{tab}"]')
        parts.append(page.inner_text("#pbody"))
    return "\n".join(parts)


def ask(page, question: str) -> None:
    page.fill("#ask", question)
    page.click("#submit")


def wait_done(page) -> None:
    page.wait_for_selector(f".sys.ok:has-text('{DONE}')", timeout=15000)


def test_auto_mode_runs_anonymously_then_reveals(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#mode button[data-v='auto'][aria-pressed='true']")
    ask(page, MEDIUM)
    wait_done(page)

    assert page.locator(".final").count() == 1
    assert "最终答案" in page.inner_text(".final")
    for label in ("作答", "互评", "修订", "汇总"):
        assert page.locator(f".phase:text-is('{label}')").count() == 1
    assert page.locator("#stage .seat .nm:has-text('组员甲')").count() == 1
    assert "s=" in page.url  # 刷新后可以回到这场讨论

    text = anonymous_text(page)
    assert "组员甲" in text and "组员乙" in text
    assert leaks(srv.identity_terms(), text) == []

    # 互评面板有结论表格
    page.click('#tabs button[data-t="reviews"]')
    assert page.locator("#pbody table tbody tr").count() >= 2

    # 揭晓后显示模型和渠道
    assert page.is_enabled("#reveal")
    page.click("#reveal")
    page.wait_for_selector("#reveal:has-text('已揭晓')")
    page.click('#tabs button[data-t="cast"]')
    revealed = page.inner_text("#pbody") + page.inner_text("#stage")
    assert leaks(srv.identity_terms(), revealed)


def test_preset_mode(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#mode button[data-v='preset']")
    page.wait_for_selector("#preset:not([hidden]) button")
    page.click("#preset button:has-text('省钱')")
    assert page.get_attribute("#preset button:has-text('省钱')", "aria-pressed") == "true"
    ask(page, MEDIUM)
    wait_done(page)
    assert "预设 · 省钱" in page.inner_text("#chat")
    page.click('#tabs button[data-t="flow"]')
    assert "小圆桌" in page.inner_text("#pbody")
    assert leaks(srv.identity_terms(), anonymous_text(page)) == []
    assert srv.env is not None
    # 预设模式不调用规划员
    assert not any("规划员" in c.messages[0].content for c in srv.env.fake.calls)


def test_manual_mode(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#mode button[data-v='manual']")
    page.wait_for_selector("#manual:not([hidden])")
    page.check("#picks input[value='b1']")
    page.check("#picks input[value='b2']")
    page.select_option("#coordinator", "f1")
    ask(page, MEDIUM)
    wait_done(page)
    assert "手动选择组员" in page.inner_text("#chat")
    # 手动勾选的模型列表收起后，会话区域仍然匿名
    page.click("#mode button[data-v='auto']")
    assert leaks(srv.identity_terms(), anonymous_text(page)) == []
    assert srv.env is not None
    members = {c.model for c in srv.env.fake.calls if "统筹" not in c.messages[0].content}
    assert members == {"b1", "b2"}


def test_manual_mode_requires_members(serve, page):
    srv = serve()
    page.goto(srv.url)
    page.click("#mode button[data-v='manual']")
    ask(page, MEDIUM)
    page.wait_for_selector("#formerr:not([hidden])")
    assert "至少勾选" in page.inner_text("#formerr")


def test_cost_card_over_threshold(serve, page):
    srv = serve(difficulty="hard", confirm_threshold_usd=0.0001)
    page.goto(srv.url)
    ask(page, MEDIUM)
    card = page.wait_for_selector(".card:not(.resolved)", timeout=15000)
    text = card.inner_text()
    assert "花费确认" in text and "推荐" in text and "$" in text
    assert page.inner_text("#pstate") == "等待你决定"
    assert leaks(srv.identity_terms(), anonymous_text(page)) == []
    page.click(".card:not(.resolved) .opt[data-opt='continue']")
    page.wait_for_selector(".card.resolved .result:has-text('已选择')")
    wait_done(page)


def test_cost_card_stop(serve, page):
    srv = serve(difficulty="hard", confirm_threshold_usd=0.0001)
    page.goto(srv.url)
    ask(page, MEDIUM)
    page.wait_for_selector(".card:not(.resolved)", timeout=15000)
    page.click(".card:not(.resolved) .opt[data-opt='stop']")
    page.wait_for_selector(".sys.bad:has-text('已停止')")
    assert page.is_enabled("#reveal")


def test_simple_question_single_answer(serve, page):
    srv = serve(difficulty="simple", confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, SHORT)
    wait_done(page)
    assert page.locator(".msg .kind:text-is('作答')").count() == 1
    assert page.locator(".final").count() == 0
    assert "单人快答" in page.inner_text("#chat")


def test_history_and_reload(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)
    url = page.url
    page.click("#new")
    assert page.locator(".welcome").count() == 1
    page.click('#tabs button[data-t="history"]')
    page.wait_for_selector("#pbody tr[data-sid]")
    page.click("#pbody tr[data-sid]")
    wait_done(page)
    page.goto(url)
    wait_done(page)
    assert page.locator(".final").count() == 1


def test_usage_and_channels_panels(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)
    page.click('#tabs button[data-t="usage"]')
    page.wait_for_selector("#pbody:has-text('本月预算')")
    usage = page.inner_text("#pbody")
    assert "$20.00" in usage and "今日上限" in usage and "按渠道花费" in usage
    page.click('#tabs button[data-t="channels"]')
    assert "可用" in page.inner_text("#pbody")
    assert "本月" in page.inner_text("#meter")


def test_narrow_screen_stacks(serve, browser):
    srv = serve(confirm_threshold_usd=100.0)
    ctx = browser.new_context(viewport={"width": 420, "height": 860})
    pg = ctx.new_page()
    pg.route("https://fonts.*/**", lambda r: r.abort())
    pg.goto(srv.url)
    pg.wait_for_selector("#stage .seat")
    stage = pg.locator("#stage").bounding_box()
    chat = pg.locator("#chat").bounding_box()
    panel = pg.locator(".panel").bounding_box()
    assert stage["y"] < chat["y"] < panel["y"]
    assert chat["width"] <= 420
    width = pg.evaluate("document.documentElement.scrollWidth")
    assert width <= 420
    ctx.close()


def test_live_progress_highlights_speakers(serve, page):
    srv = serve(delay=0.6, confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, MEDIUM)
    page.wait_for_selector("#stage .seat.speaking", timeout=15000)
    page.wait_for_selector("#typing:not([hidden])")
    assert "正在" in page.inner_text("#typing")
    step = page.inner_text("#plate-step")
    assert step in {"作答", "互评", "修订", "汇总"}
    assert page.inner_text("#pstate") == "进行中"
    page.wait_for_selector(".pn.now")
    assert not page.is_enabled("#reveal")  # 讨论结束前不能揭晓
    wait_done(page)
    assert page.locator("#stage .seat.speaking").count() == 0
    assert page.is_hidden("#typing")
