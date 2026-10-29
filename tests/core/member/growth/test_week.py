"""一周的经历：日记按句、记下的事、用户说的话，带编号；标出受用户影响的行；只看这一周、只看自己的。"""

from __future__ import annotations

from datetime import UTC

from roundtable.core.chat import ChatHub
from roundtable.core.member.growth import build, monday, week_id

from ...orchestrator.conftest import Env
from .common import MONDAY, WEEK, growth_env, setup


def test_week_id_and_monday():
    from datetime import date

    assert week_id(MONDAY) == WEEK and monday(date(2026, 10, 25)) == MONDAY


def test_week_lines_are_numbered_by_day_and_flag_user():
    env = Env()
    setup(env)
    ChatHub(env.rt).room("work")
    env.rt.repo.add_chat_message(
        "work", "user", "工作群里的话", created_at="2026-10-22T12:00:00+00:00"
    )
    week = build(env.rt, "V1", MONDAY, UTC)
    texts = [ln.text for ln in week.lines]
    assert week.week == WEEK and week.days == 4
    assert texts[:2] == ["今天又去天文台看星星，越看越着迷。", "晚上和阿三聊了很久的黑洞。"]
    assert [ln.n for ln in week.lines] == list(range(1, len(week.lines) + 1))
    sources = {ln.text: ln.source for ln in week.lines}
    assert sources["在天文台待了一晚上看流星"] == "item"
    assert sources["交了一份关于极值的作业"] == "work"
    assert sources["你最近好像特别爱看星星"] == "user"
    user = {ln.text for ln in week.lines if ln.user}
    assert user == {
        "用户说我太较真了，我想了想，有些事确实可以放松一点。",
        "你最近好像特别爱看星星",
    }
    joined = week.text()
    assert "上上周" not in joined and "阿二" not in joined and "工作群" not in joined
    assert "1. [10-19 日记] 今天又去天文台" in joined


def test_week_respects_material_budget():
    env = growth_env(material_chars=500, line_chars=200)
    setup(env)
    env.rt.repo.add_memory("V1", "day", "很长的一句。" * 80, period="2026-10-20")
    week = build(env.rt, "V1", MONDAY, UTC)
    assert sum(len(ln.text) + 12 for ln in week.lines) <= 500


def test_empty_week():
    env = Env()
    week = build(env.rt, "V2", MONDAY, UTC)
    assert week.lines == [] and week.days == 0 and week.text() == "（没有）"
