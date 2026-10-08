"""前端端到端：各档位与自选；匿名开 / 关；超门槛的确认卡片；匿名讨论揭晓前不显示身份。"""

from __future__ import annotations

import re

from ..core.orchestrator.conftest import MEDIUM

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


def test_anonymous_budget_tier_then_reveal(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#tier button[data-v='budget'][aria-pressed='true']")  # 默认便宜档
    assert not page.is_checked("#anonymous")  # 匿名默认关闭
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)

    assert page.locator(".final").count() == 1
    assert "最终答案" in page.inner_text(".final")
    for label in ("作答", "互评", "修订", "汇总"):
        assert page.locator(f".phase:text-is('{label}')").count() == 1
    assert page.locator("#stage .seat .nm:has-text('组员甲')").count() == 1
    assert "便宜档全员 · 匿名" in page.inner_text("#chat")
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


def test_not_anonymous_shows_models_from_the_start(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, MEDIUM)
    wait_done(page)
    stage = page.inner_text("#stage")
    assert {"b1", "b2", "b3"} <= set(leaks(srv.identity_terms(), stage))  # 全员都显示真实模型
    assert page.is_hidden("#reveal")  # 没有揭晓步骤
    page.click('#tabs button[data-t="cast"]')
    assert "作答 · c · $" in page.inner_text("#pbody")  # 每次调用的渠道直接显示


def test_flagship_tier(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#tier button[data-v='flagship']")
    assert page.get_attribute("#tier button[data-v='flagship']", "aria-pressed") == "true"
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    assert "旗舰档全员" in page.inner_text("#chat")
    assert page.locator("#stage .seat .nm:has-text('组员')").count() == 4
    assert leaks(srv.identity_terms(), anonymous_text(page)) == []
    assert srv.env is not None
    called = {c.model for c in srv.env.fake.calls if "规划员" not in c.messages[0].content}
    assert called == {"f1", "f2", "f3", "f4", "f5"}  # 5 个旗舰全部上桌


def test_custom_tier(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.click("#tier button[data-v='custom']")
    page.wait_for_selector("#manual:not([hidden])")
    for m in ("b1", "b2", "f1"):
        page.check(f"#picks input[value='{m}']")
    page.select_option("#coordinator", "f1")
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    assert "自选" in page.inner_text("#chat")
    # 勾选列表收起后，会话区域仍然匿名
    page.click("#tier button[data-v='budget']")
    assert leaks(srv.identity_terms(), anonymous_text(page)) == []
    assert srv.env is not None
    members = {c.model for c in srv.env.fake.calls if "学习小组的统筹" not in c.messages[0].content}
    assert members == {"b1", "b2"}


def test_custom_tier_requires_enough_models(serve, page):
    srv = serve()
    page.goto(srv.url)
    page.click("#tier button[data-v='custom']")
    page.check("#picks input[value='b1']")
    page.check("#picks input[value='b2']")
    ask(page, MEDIUM)
    page.wait_for_selector("#formerr:not([hidden])")
    assert "至少勾选 3 个" in page.inner_text("#formerr")


def test_cost_card_over_threshold(serve, page):
    srv = serve(difficulty="hard", confirm_threshold_usd=0.0001)
    page.goto(srv.url)
    page.check("#anonymous")
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
    page.check("#anonymous")
    ask(page, MEDIUM)
    page.wait_for_selector(".card:not(.resolved)", timeout=15000)
    page.click(".card:not(.resolved) .opt[data-opt='stop']")
    page.wait_for_selector(".sys.bad:has-text('已停止')")
    assert page.is_enabled("#reveal")


def test_escalation_is_always_asked(serve, page):
    srv = serve(resolved=False, confirm_threshold_usd=100.0)
    page.goto(srv.url)
    ask(page, MEDIUM)
    card = page.wait_for_selector(".card[data-kind='escalation']:not(.resolved)", timeout=15000)
    assert "旗舰档全员" in card.inner_text()
    page.click(".card:not(.resolved) .opt[data-opt='accept']")
    wait_done(page)
    assert page.locator(".final").count() == 1


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


def test_lazy_member_flagged_and_contributions_tab(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    assert srv.env is not None
    original = srv.env.reply

    def lazy_b1(model, messages):
        if model == "b1" and "独立完成同一道题" in messages[0].content:
            return "略"  # 重做后仍然敷衍
        return original(model, messages)

    srv.env.fake._default = lazy_b1
    page.goto(srv.url)
    page.click("#tier button[data-v='custom']")
    for m in ("b1", "b2", "f1"):
        page.check(f"#picks input[value='{m}']")
    page.select_option("#coordinator", "f1")
    ask(page, MEDIUM)
    wait_done(page)
    assert page.locator(".sys.bad:has-text('标记为敷衍')").count() == 1
    assert page.locator(".msg .tagp.bad:has-text('敷衍')").count() == 1
    assert page.locator("#stage .seat.lazy").count() == 1
    page.click('#tabs button[data-t="contrib"]')
    page.wait_for_selector("#pbody table")
    panel = page.inner_text("#pbody")
    assert "本场" in panel and "被采纳" in panel and "敷衍" in panel
    page.wait_for_selector("#pbody h4:has-text('历史') + table")  # 匿名关闭：计入历史
    assert "b1" in page.inner_text("#pbody")


def test_collab_mode(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#workflow button[data-v='discussion'][aria-pressed='true']")
    page.click("#workflow button[data-v='collab']")
    page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    chat = page.inner_text("#chat")
    for label in ("拆分子任务", "自荐", "分配", "完成子任务", "交叉审查", "修改", "合并"):
        assert page.locator(f".phase:text-is('{label}')").count() == 1, label
    assert "协同 · 便宜档全员 · 匿名" in chat and "拆分为 2 个子任务" in chat
    assert page.locator(".final h3:has-text('合并成果')").count() == 1
    assert "采纳情况" in page.inner_text(".final")
    page.click('#tabs button[data-t="reviews"]')
    assert "W1（T1 ·" in page.inner_text("#pbody")
    page.click('#tabs button[data-t="flow"]')
    assert "拆分子任务" in page.inner_text("#pbody")
    assert leaks(srv.identity_terms(), anonymous_text(page)) == []
