"""接力：一场结束后点「转协同作业」「交给一个人单独做」，下一场带上上一场的成果摘要。"""

from __future__ import annotations

from ..core.orchestrator.conftest import MEDIUM
from .test_e2e import ask, wait_done


def test_carry_buttons_start_next_session_with_summary(serve, page):
    srv = serve(confirm_threshold_usd=100.0)
    page.goto(srv.url)
    page.wait_for_selector("#legend .row")
    page.click("#workflow button[data-v='discussion']")
    ask(page, MEDIUM)
    wait_done(page)

    page.click(".carry-bar [data-carry='collab']")
    page.wait_for_selector("#carry:not([hidden]) .carry-head")
    assert page.get_attribute("#workflow button[data-v='collab']", "aria-pressed") == "true"
    assert "接着上一场" in page.inner_text("#carry")
    first = page.evaluate("location.hash")
    # 题目留空也能提交：用默认要求
    page.click("#submit")
    page.wait_for_function(f"location.hash !== {first!r}", timeout=10000)
    page.wait_for_selector("#question:has-text('previous_result')", timeout=10000)
    assert page.is_hidden("#carry")
    wait_done(page)

    page.click(".carry-bar [data-carry='solo']")
    page.wait_for_selector("#carry-who")
    model = page.eval_on_selector("#carry-who", "s => s.options[1].value")
    page.select_option("#carry-who", model)
    page.fill("#ask", "单独写一版")
    second = page.evaluate("location.hash")
    page.click("#submit")
    page.wait_for_function(f"location.hash !== {second!r}", timeout=10000)
    wait_done(page)
    sid = page.evaluate("location.hash").split("=", 1)[1]
    seats = page.request.get(f"{srv.url}/api/sessions/{sid}").json()["seats"]
    assert [s["model_id"] for s in seats if s["role"] != "coordinator"] == [model]

    # 取消接力
    page.click(".carry-bar [data-carry='discussion']")
    page.wait_for_selector("#carry:not([hidden])")
    page.click("[data-act='carry-off']")
    assert page.is_hidden("#carry")
