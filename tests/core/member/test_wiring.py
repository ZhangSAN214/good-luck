"""接线：节日和看到的东西进念头、节日更想约人；朋友圈读大脑的心事；
目标起步读 P6 熟练度；偶尔改口。"""

from __future__ import annotations

import random
from datetime import timedelta

from roundtable.core.member.brain import Brain
from roundtable.core.member.bus import Event, EventBus, perception

from .test_brain import NOON, setup
from .test_human import _human
from .test_human import lines as human_lines
from .test_love import NAMES


def engine_brain(env):
    bus = EventBus(env.rt)
    return Brain(env.rt, bus, members=lambda: ["V1", "V2"], names=NAMES), bus


def test_holidays_and_things_seen_become_thoughts_and_fade():
    env, _ = setup()
    brain, bus = engine_brain(env)
    day = {"items": [{"kind": "holiday", "name": "国庆节"}, {"kind": "term", "name": "寒露"}]}
    bus.publish(Event("calendar.day", NOON, None, day))
    bus.publish(perception("web", NOON, "V1", "一篇讲深海鱼会发光的文章", "某站"))
    s = brain._load("V1")
    texts = [t.text for t in brain.thoughts("V1", s, NOON, random.Random(0))]
    assert "今天是国庆节" in texts and not any("寒露" in t for t in texts)
    assert any(t.startswith("上网看到：一篇讲深海鱼") for t in texts)
    assert any(p["text"] == "今天是国庆节" for p in brain._load("V2").percepts)  # 全员都知道
    assert not any("深海鱼" in p["text"] for p in brain._load("V2").percepts)  # 只有看到的人知道
    later = [t.text for t in brain.thoughts("V1", s, NOON + timedelta(days=1), random.Random(0))]
    assert not any("国庆节" in t or "深海鱼" in t for t in later)
    assert brain._festive(s, NOON) and not brain._festive(s, NOON + timedelta(days=1))


def test_moments_read_what_is_on_her_mind():
    env, brain = setup()
    s = brain._load("V1")
    brain._news(s, "「弹吉他」终于能完整弹一首了", NOON, 0.9)
    brain._save("V1", s)
    assert "弹吉他" in brain.musing("V1", NOON)


def test_new_goals_start_ahead_in_domains_she_already_knows_well():
    env, brain = setup()
    for i in range(40):
        env.rt.repo.add_memory_item("V1", f"复习考试第{i}章", day="2026-10-01", domain="学术")
    goal = brain._new_goal("V1", "准备期中考试")
    fresh = brain._new_goal("V2", "准备期中考试")
    assert goal["domain"] == "学术" and goal["xp"] > fresh["xp"] == 0


def test_occasionally_corrects_herself_midway():
    env, _ = setup()
    _human(env, correct_chance=1.0)
    from roundtable.core.member.human import Human

    got = dict(human_lines(Human(env.rt, names=NAMES)))
    assert "不对，是" in got["correct"]
