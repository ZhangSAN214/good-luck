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
