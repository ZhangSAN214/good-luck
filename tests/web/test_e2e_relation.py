"""成员关系：「成员设定与记忆」里的关系——和你到了哪一步、和同事有多熟、她对同事的看法、清空。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.member.relation import USER, RelationPlugin

from .test_e2e_chat import open_chat

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)


def prepare(env):
    env.rt.budget.now = lambda: NOW
    rel = RelationPlugin(env.rt)
    for d in range(4):
        at = NOW - timedelta(days=d)
        for _ in range(5):
            rel.add("V1", USER, at, 1.0, "dm")
        for a, b in (("V1", "V2"), ("V2", "V1")):
            rel.add(a, b, at, 1.0, "gather", "canteen")


def test_people_dialog_shows_relation_and_clears(serve, page):
    srv = serve(setup=prepare)
    open_chat(srv, page)
    page.click(".gc-room[data-room='dm:V1']")
    page.click("#gc-people")
    page.wait_for_selector("#people[open] #pp-rel .rl-stage")
    text = page.inner_text("#pp-rel")
    assert "熟悉" in text and "认识第 4 天" in text and "一起聊过 4 天、20 次来回" in text
    assert "到「老朋友」" in text
    assert "常在食堂碰到（4 次）" in text
    page.click("#pp-rel [data-rclear]")
    page.click("#pp-rel [data-rclear]")
    page.wait_for_selector("#pp-rel .hint >> text=还是新邻居")


def prepare_views(env):
    from ..core.member.test_insight import NOW as MONDAY
    from ..core.member.test_insight import prepare as insight_prepare

    insight_prepare(env)
    env.rt.budget.now = lambda: MONDAY


def test_people_dialog_thinks_about_you_and_drops_a_view(serve, page):
    srv = serve(setup=prepare_views)
    open_chat(srv, page)
    page.click(".gc-room[data-room='dm:V1']")
    page.click("#gc-people")
    page.wait_for_selector("#people[open] #pp-rel [data-rthink]")
    page.click("#pp-rel [data-rthink]")
    page.wait_for_selector("#pp-rel [data-rdrop]")
    text = page.inner_text("#pp-rel")
    assert "我觉得你挺能熬的，但别太晚睡" in text and "不太同意你" in text
    assert "从这几条来：「喜欢深夜写代码」" in text
    assert page.locator("#pp-rel [data-rthink]").count() == 0  # 每周一次
    page.click("#pp-rel [data-rdrop='0']")
    page.wait_for_selector("#pp-rel [data-rdrop='1']", state="detached")  # 删完重画只剩一句
    assert "挺能熬" not in page.inner_text("#pp-rel")
