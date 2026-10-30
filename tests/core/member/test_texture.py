"""前面建议的 7 样：记忆走样、认知失调、言行不一、面子和比较、内部梗、三观慢慢变、身体节律。"""

from __future__ import annotations

import random
from datetime import timedelta

from roundtable.core.hometz import home_zone
from roundtable.core.member.brain import IntentCard, Thought
from roundtable.core.member.human import Human
from roundtable.core.member.love import USER, Heart
from roundtable.core.storage.repository import iso

from .test_brain import NOON, setup
from .test_human import DM, _human
from .test_human import lines as human_lines
from .test_love import NAMES, PAIR, bond, card, say, world
from .test_psyche import RULES, _with


def test_old_memories_cued_up_may_be_misremembered():
    env, brain = setup()
    assoc = RULES.assoc.model_copy(update={"cue_memory": 1.0, "drift_chance": 1.0})
    _with(env, assoc=assoc)
    env.rt.repo.add_memory_item("V1", "和用户去了一兰拉面店", day="2026-08-01", importance=2)
    texts, tags = [], set()
    s = brain._load("V1")
    for i in range(5):
        mine: list[Thought] = []
        t: list[str] = []
        brain._psyche_thoughts("V1", s, mine, t, "拉面店还开着吗", {}, {}, NOON, random.Random(i))
        texts += [m.text for m in mine if m.source == "cue"]
        tags |= set(t)
    assert texts and all("记得不太清" in x for x in texts) and "drift" in tags


def test_after_giving_something_up_she_tells_herself_she_did_not_want_it():
    env, brain = setup()
    s = brain._load("V1")
    s.dropped = [{"name": "练琴", "at": iso(NOON - timedelta(days=1))}]
    texts = [t.text for t in brain.thoughts("V1", s, NOON, random.Random(0))]
    assert any("其实也没那么想做" in t for t in texts)
    texts = [t.text for t in brain.thoughts("V1", s, NOON + timedelta(days=5), random.Random(0))]
    assert not any("没那么想做" in t for t in texts)


def test_broken_promises_come_back_as_a_little_embarrassment():
    env, _ = setup()
    _human(env, broken_chance=1.0)
    human = Human(env.rt, names=NAMES)
    human.note("V1", "下次我请你吃火锅", NOON, to=USER)
    later = NOON + timedelta(days=10)
    texts = [t for t, _ in human.thoughts("V1", later, {"V1"}, random.Random(0))]
    assert any("一直没做" in t and "火锅" in t for t in texts)


def test_good_news_from_someone_not_close_brings_a_quiet_comparison():
    env, heart, _ = world()
    spot = Thought("goal", "「练琴」有点样子了", 0.9)
    heart.after(card("V1", "share", "V3", spotlight=spot), NOON)  # V3 不在乎 V1
    texts = [t for t, _ in heart.thoughts("V3", NOON, present={"V3", "V1"})]
    assert any("心里有点比较" in t for t in texts)
    sad = Thought("goal", "「练琴」最近没什么进展，有点泄气", 0.6)
    heart.after(card("V1", "share", "V2", spotlight=sad), NOON)
    assert not any("比较" in t for t, _ in heart.thoughts("V2", NOON, present={"V2"}))


def test_shared_laughs_become_inside_jokes():
    env, heart, bus = world()
    for i in range(3):
        at = NOON + timedelta(days=i)
        say(env, bus, PAIR, "V1", "那只猫又偷吃了哈哈", at)
        say(env, bus, PAIR, "V2", "笑死", at + timedelta(minutes=1))
    v = heart.view("V2", "V1", NOON + timedelta(days=3))
    assert v["moments"] >= 2 and "那只猫又偷吃了哈哈" in v["jokes"]
    got = [heart.line("V2", "V1", NOON + timedelta(days=3), random.Random(i)) for i in range(40)]
    assert any(x and x[0] == "joke" and "那只猫" in x[1] for x in got)


def test_values_drift_toward_people_she_cares_about_only_when_spoken():
    env, brain = setup()
    bond(Heart(env.rt), "V1", "V2", care=0.8)
    lean = "自由|1"
    c = IntentCard("V1", "respond", "…", None, 1.0, (), to="V2", lean=lean)
    brain.after(c, NOON)
    brain.after(c, NOON)
    assert brain._load("V1").shifts["自由"] == 0.1
    c3 = IntentCard("V1", "respond", "…", None, 1.0, (), to="V3", lean=lean)
    brain.after(c3, NOON)  # 不在乎 V3：不挪
    assert brain._load("V1").shifts["自由"] == 0.1


def test_body_rhythm_follows_the_local_clock():
    env, _ = setup()
    _human(env, body_chance=1.0)
    home_zone(env.rt).set("UTC")
    human = Human(env.rt, names=NAMES)
    after_lunch = NOON.replace(hour=14)
    assert any(t == "body" and "犯困" in x for t, x in human_lines(human, at=after_lunch))
    assert not any(t == "body" for t, _ in human_lines(human, at=NOON.replace(hour=16)))
    assert DM
