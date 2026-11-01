"""前端：聊天里的活交给办公室（docs/CHAT.md G14）。

交办卡片、选项按钮、结果卡片（表格、图、公式）、发文件。"""

from __future__ import annotations

from roundtable.core.chat import GroupChat
from tests.web.conftest import wait_until


def click_until(page, sel, done, tries=5):
    """点一下等结果；轮询刷新正好把按钮换掉时点击会落空，再点一次。"""
    for _ in range(tries):
        if page.locator(sel).count() and page.locator(sel).first.is_enabled():
            page.click(sel)
        try:
            wait_until(done, timeout=2)
            return
        except AssertionError:
            continue
    loc = page.locator(sel)
    seen = {
        "found": loc.count(),
        "enabled": loc.count() and loc.first.is_enabled(),
        "error": page.locator("#gc-err").inner_text() if page.locator("#gc-err").count() else "",
        "responses": getattr(page, "handoff_log", []),
        "js_errors": getattr(page, "js_errors", []),
    }
    raise AssertionError(f"点了 {sel} 没有反应：{seen}")


def open_chat(srv, page):
    page.goto(srv.url + "/#m=chat")
    page.wait_for_selector("#gc-members button[data-name]")


def test_handoff_card_dismiss_and_choice_buttons(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    chat = GroupChat(srv.env.rt, "main")
    msg = chat.send_as(
        "V1",
        "这个我让办公室跑一下",
        meta={"handoff": {"task": "按九月数据模拟十月收益", "state": "pending"}},
    )
    pick = chat.send_as("V2", "你想先看哪个", meta={"choices": ["收益曲线", "持仓明细"]})
    page.handoff_log = []
    page.on(
        "response",
        lambda r: (
            "/api/chat/handoff" in r.url
            and page.handoff_log.append(f"{r.status} {r.url.split('/api/')[-1]} {r.text()[:120]}")
        ),
    )
    open_chat(srv, page)
    card = f".gc-msg[data-id='{msg['id']}'] .wc-handoff"
    page.wait_for_selector(card)
    assert "按九月数据模拟十月收益" in page.inner_text(card)
    assert "[交办" not in page.inner_text("#gc-feed")
    state = lambda: repo.chat_message(msg["id"])["meta"]["handoff"]["state"]  # noqa: E731
    click_until(page, f"{card} [data-handoff-dismiss]", lambda: state() == "dismissed")
    page.wait_for_selector(f"{card}.off")
    mine = lambda: [m["text"] for m in repo.chat_messages("main") if m["author"] == "user"]  # noqa: E731
    choice = f".gc-msg[data-id='{pick['id']}'] [data-choice='持仓明细']"
    click_until(page, choice, lambda: "持仓明细" in mine())
    gone = f".gc-msg[data-id='{pick['id']}'] .wc-choices"
    page.wait_for_function(f'!document.querySelector("{gone}")')


def test_work_card_renders_table_chart_and_formula(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    text = (
        "十月模拟结果如下。\n\n| 日期 | 收益 |\n|---|---|\n| 10-08 | +1.2% |\n\n"
        "```mermaid\ngraph LR\n  A[本金] --> B[银行股]\n```\n\n"
        "$$r = \\frac{V_1 - V_0}{V_0}$$\n"
    )
    card = {
        "session_id": "s-x",
        "title": "模拟十月收益",
        "ok": True,
        "text": text,
        "cut": False,
        "files": [],
        "cost_usd": 0.0123,
    }
    meta = {"work_card": card, "auto_text": True}
    msg = GroupChat(srv.env.rt, "main").send_as("V1", "[办公室做好了]", meta=meta)
    open_chat(srv, page)
    sel = f".gc-msg[data-id='{msg['id']}']"
    page.wait_for_selector(f"{sel} .wc-card")
    assert "[办公室做好了]" not in page.inner_text(sel)
    assert page.locator(f"{sel} .wc-md table").count() == 1
    page.wait_for_selector(f"{sel} .wc-chart svg", timeout=15000)
    page.wait_for_selector(f"{sel} .wc-math .katex", timeout=15000)
    assert "$0.0123" in page.inner_text(sel)


def test_file_button_uploads_and_sends_a_file_chip(serve, page, tmp_path):
    srv = serve(confirm_threshold_usd=100.0)
    repo = srv.env.rt.repo
    f = tmp_path / "账单.csv"
    f.write_text("日期,金额\n10-01,30\n", encoding="utf-8")
    open_chat(srv, page)
    page.wait_for_selector("#gc-file")
    page.set_input_files("#gc-file-input", str(f))
    page.wait_for_selector("#gc-filebar .wc-file")
    page.fill("#chat-input", "帮我看看这个")
    page.keyboard.press("Enter")
    sent = lambda: [m for m in repo.chat_messages("main") if m["author"] == "user"]  # noqa: E731
    wait_until(lambda: any((m["meta"] or {}).get("files") for m in sent()))
    page.wait_for_selector(".gc-msg.me .wc-file")
    assert "账单.csv" in page.inner_text(".gc-msg.me")
    assert "[文件" not in page.inner_text(".gc-msg.me .gc-bub")
    assert page.is_hidden("#gc-filebar")
