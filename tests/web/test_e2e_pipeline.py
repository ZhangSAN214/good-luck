"""前端：协同流水线的交接链、子任务类型、过程区的交接提示、昵称与大米。"""

from __future__ import annotations

import re

from ..core.orchestrator.conftest import MEDIUM
from ..core.orchestrator.test_media import script
from ..core.orchestrator.test_pipeline import WHOLE
from .test_e2e import anonymous_text, ask, leaks, wait_done


def start_pipeline(serve, page, anonymous=True):
    srv = serve(confirm_threshold_usd=100.0, pipeline=True)
    script(
        srv.env,
        **{"把任务拆成子任务": lambda m, msgs: WHOLE, "没有通过代码检查": lambda m, msgs: WHOLE},
    )
    page.goto(srv.url)
    page.wait_for_selector("#workflow button[data-v='discussion'][aria-pressed='true']")
    page.click("#workflow button[data-v='collab']")
    if anonymous:
        page.check("#anonymous")
    ask(page, MEDIUM)
    wait_done(page)
    return srv


def test_chain_graph_kinds_and_handoffs_in_the_feed(serve, page):
    srv = start_pipeline(serve, page)
    chat = page.inner_text("#chat")
    assert "已按模板「文档」生成" in chat
    assert "把〈提纲与要点〉交给" in chat  # 过程区：谁把什么交给谁
    assert page.locator("#chat .sys:has-text('交接')").count() >= 3
    page.click('#tabs button[data-t="split"]')
    panel = page.locator("#pbody")
    assert panel.locator(".chain .node").count() == 4
    assert panel.locator(".chain .col").count() == 4  # 分析 → 撰写 → 审查 → 整合
    kinds = panel.locator(".chain .node .pill.kind").all_inner_texts()
    assert kinds == ["分析", "撰写", "审查", "整合"]
    assert "← T1：提纲与要点" in panel.inner_text()
    assert panel.locator(".chain .node.done").count() == 4
    # 子任务表里每个子任务带类型
    assert panel.locator("table tbody tr .pill.kind").count() >= 4
    # 匿名：全程只有塔罗牌代号，没有昵称、模型名
    assert leaks(srv.anonymous_terms(), anonymous_text(page)) == []


def test_non_anonymous_pipeline_names_members_by_nickname(serve, page):
    srv = start_pipeline(serve, page, anonymous=False)
    page.click('#tabs button[data-t="split"]')
    text = page.inner_text("#pbody")
    nicknames = set(srv.env.config.personas.nicknames.values())
    assert any(f"{n}·节电" in text for n in nicknames)
    assert re.search(r"[^\s]+·节电 把〈", page.inner_text("#chat"))
