"""修复轮 B 的网页改动（端到端）：多媒体题目建议切换到协同模式、截断不算敷衍、
敷衍原因、Markdown 表格、本地重置时间。
"""

from __future__ import annotations

from roundtable.core.providers import RawCompletion

from ..core.orchestrator.conftest import MEDIUM
from .test_e2e import ask, wait_done

MULTI = "画四张不同风格的插画，然后拼成一张长图"
ANSWER_SYSTEM = "独立完成同一道题"


def custom_lineup(page) -> None:
    """自选 b1、b2 当组员，f1 当统筹（b1 的作答由各测试改写）。"""
    page.click("#tier button[data-v='custom']")
    for m in ("b1", "b2", "f1"):
        page.check(f"#picks input[value='{m}']")
    page.select_option("#coordinator", "f1")


def b1_answers(srv, outcome) -> None:
    original = srv.env.reply

    def reply(model, messages):
        if model == "b1" and ANSWER_SYSTEM in messages[0].content:
            return outcome
        return original(model, messages)

    srv.env.fake._default = reply


def test_multi_media_advice_switches_to_collab_in_one_click(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#workflow button[data-v='discussion'][aria-pressed='true']")
    page.fill("#ask", MULTI)
    page.wait_for_selector("#estimate .advice[data-advice='multi_media']")
    advice = page.inner_text("#estimate .advice")
    assert "建议切换到协同模式" in advice and "四张不同风格的插画" in advice
    assert page.locator("#estimate table").count() == 1  # 建议在预估表格上方，表格照常显示

    page.click("#estimate [data-switch-workflow='collab']")
    page.wait_for_selector("#workflow button[data-v='collab'][aria-pressed='true']")
    assert page.locator("#workflow button[data-v='discussion'][aria-pressed='true']").count() == 0
    page.wait_for_selector("#media-auto")  # 提问区跟着切换（输出类型由统筹决定）
    assert "交叉审查" in page.inner_text("#legend")
    # 重新预估后（协同模式）不再提示
    page.wait_for_function("!document.querySelector('#estimate .advice')")
    page.wait_for_selector("#estimate table")
    assert srv.env.fake.calls == []  # 只是规则判断，不调用模型

    # 切回讨论模式，建议重新出现；普通题目没有建议
    page.click("#workflow button[data-v='discussion']")
    page.wait_for_selector("#estimate .advice")
    page.fill("#ask", MEDIUM)
    page.wait_for_function("!document.querySelector('#estimate .advice')")


def test_truncated_answer_is_shown_as_not_lazy(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    b1_answers(srv, RawCompletion("先求导", truncated=True, finish_reason="length"))
    page.goto(srv.url)
    custom_lineup(page)
    ask(page, MEDIUM)
    wait_done(page)
    note = page.locator(".sys:has-text('被长度上限截断，未判为敷衍')")
    assert note.count() == 1
    text = note.inner_text()
    assert "内容过短" in text and "门槛" in text
    assert text.count("截断") == 1  # 不重复说"被截断"
    assert page.locator(".sys.bad:has-text('标记为敷衍')").count() == 0
    assert page.locator(".msg .tagp.bad:has-text('敷衍')").count() == 0
    assert page.locator("#stage .seat.lazy").count() == 0


def test_lazy_tag_shows_the_reason(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    b1_answers(srv, "最大值 2，最小值 -2。")
    page.goto(srv.url)
    custom_lineup(page)
    ask(page, MEDIUM)
    wait_done(page)
    tag = page.locator(".msg .tagp.bad:has-text('敷衍')")
    assert tag.count() == 1
    why = page.locator(".msg .tagp.bad + .why")
    assert why.count() == 1
    reason = why.inner_text()
    assert "门槛" in reason  # 原因带实测数值
    assert why.get_attribute("title") == reason and tag.get_attribute("title") == reason


def test_markdown_table_with_br_renders_in_answer(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    table = (
        "极值点与端点的比较如下，先求导 f'(x)=3x^2-3，令其为零得 x=±1，再与端点比较。\n\n"
        "| x | f(x) |\n|---|---|\n| -2 | -2<br>（端点） |\n| 1 | **-2** |\n\n"
        "所以最大值是 2，最小值是 -2，取得最值的点分别列在表格中。<script>window.__pwn=1</script>"
    )
    b1_answers(srv, table)
    page.goto(srv.url)
    custom_lineup(page)
    ask(page, MEDIUM)
    wait_done(page)
    t = page.locator(".bubble table.mdt").first
    t.wait_for()
    assert t.locator("thead th").all_inner_texts() == ["x", "f(x)"]
    assert t.locator("tbody tr").count() == 2
    first = t.locator("tbody tr").first.locator("td").nth(1)
    assert first.inner_html() == "-2<br>（端点）"
    assert t.locator("tbody b").inner_text() == "-2"
    assert page.evaluate("window.__pwn") is None  # 其余 HTML 仍被转义
    assert "&lt;script&gt;" in page.locator(".bubble:has(table.mdt)").first.inner_html()


def test_budget_reset_time_shows_utc_and_local(serve, browser):
    srv = serve(confirm_threshold_usd=100.0)
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, timezone_id="Asia/Shanghai")
    try:
        pg = ctx.new_page()
        pg.route("https://fonts.googleapis.com/**", lambda r: r.abort())
        pg.route("https://fonts.gstatic.com/**", lambda r: r.abort())
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(srv.url)
        pg.click('#tabs button[data-t="usage"]')
        pg.wait_for_selector("#pbody .hint:has-text('重置')")
        hint = pg.locator("#pbody .hint:has-text('重置')").first.inner_text()
        assert "UTC" in hint and "本地时间" in hint and "UTC+8" in hint, hint
        assert errors == []
    finally:
        ctx.close()
