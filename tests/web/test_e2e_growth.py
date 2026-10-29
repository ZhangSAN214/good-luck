"""成员成长：「成员设定与记忆」里的成长时间线——现在反思上一周、看到改了什么和引用的经历、撤回一条。"""

from __future__ import annotations

from ..core.member.growth.common import NOW, fake_growth, setup
from .test_e2e_chat import open_chat


def prepare(env):
    env.rt.budget.now = lambda: NOW
    setup(env)
    fake_growth(env)


def test_people_dialog_shows_growth_timeline_and_reverts(serve, page):
    srv = serve(confirm_threshold_usd=100.0, setup=prepare)
    open_chat(srv, page)
    page.click(".gc-room[data-room='dm:V1']")
    page.click("#gc-people")
    page.wait_for_selector("#people[open] #pp-grow [data-grun]")
    assert "还没有成长记录" in page.inner_text("#pp-grow")
    assert "熟练度：" in page.inner_text("#pp-grow") and "娱乐 1 级" in page.inner_text("#pp-grow")
    page.click("#pp-grow [data-grun]")
    page.wait_for_selector("#pp-grow li.gr-change")
    text = page.inner_text("#pp-grow")
    assert "性格分·好奇 3 → 4" in text and "喜好·最爱 加上「看星星」" in text
    assert "评审 4 分" in text and "引用的经历：「越看越着迷」" in text
    assert page.locator("#pp-grow [data-grun]").count() == 0  # 每周只反思一次
    page.click("#pp-grow li.gr-change [data-grevert]")
    page.wait_for_selector("#pp-grow li.gr-revert")
    assert "已撤回" in page.inner_text("#pp-grow")
