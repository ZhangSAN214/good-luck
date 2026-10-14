"""前端：协同的进度看板与分工讨论显示。"""

from __future__ import annotations

from ..core.orchestrator.conftest import MEDIUM
from ..core.orchestrator.test_media import script
from ..core.orchestrator.test_pipeline import WHOLE
from .test_e2e import ask, wait_done


def test_board_and_discussion_are_shown(serve, page):
    srv = serve(confirm_threshold_usd=100.0, pipeline=True)
    script(
        srv.env,
        **{"把任务拆成子任务": lambda m, msgs: WHOLE, "没有通过代码检查": lambda m, msgs: WHOLE},
    )
    page.goto(srv.url)
    page.wait_for_selector("#workflow button[data-v='discussion'][aria-pressed='true']")
    page.click("#workflow button[data-v='collab']")
    ask(page, MEDIUM)
    wait_done(page)
    page.click('#tabs button[data-t="split"]')
    panel = page.locator("#pbody")
    assert panel.locator(".board .bcard").count() >= 4
    assert (
        panel.locator(".board .bcard[data-state='已完成']").count()
        == panel.locator(".board .bcard").count()
    )
    assert "进度看板" in panel.inner_text()
    assert "逐条验收" in page.inner_text("#chat")  # 合并前的验收记录出现在过程区
