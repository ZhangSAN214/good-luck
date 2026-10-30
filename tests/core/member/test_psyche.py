"""人的特质：性格天平（每天、每个场合浮动；两种相反的想法同时都强）、情绪余波与各人的消化方式、
发散联想与被勾起的旧记忆、三观立场、突发念头、先果后因；都写进意图卡，可复现、不认人。"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

from roundtable.core.config.loader import load_config
from roundtable.core.member import psyche
from roundtable.core.member.brain import Brain
from roundtable.core.member.bus import EventBus

from .test_brain import NOON, setup

RULES = load_config("config").roundtable.chat.engine.brain.psyche
CALM = dict.fromkeys(psyche.TRAITS, 3)


def _with(env, **update) -> None:
    """改 psyche 的几项配置（测试用）。"""
    cfg = env.rt.config.roundtable
    ps = cfg.chat.engine.brain.psyche.model_copy(update=update)
    brain = cfg.chat.engine.brain.model_copy(update={"psyche": ps})
    engine = cfg.chat.engine.model_copy(update={"brain": brain})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def test_sway_is_stable_within_a_day_varies_across_days_and_follows_the_situation():
    day1 = psyche.sway(RULES.sway, CALM, "V1", NOON)
    assert day1 == psyche.sway(RULES.sway, CALM, "V1", NOON + timedelta(hours=3))
    days = [psyche.sway(RULES.sway, CALM, "V1", NOON + timedelta(days=d)) for d in range(10)]
    assert len({d["外向"] for d in days}) > 3
    rested = psyche.sway(RULES.sway, CALM, "V1", NOON, energy=0.8)
    tired = psyche.sway(RULES.sway, CALM, "V1", NOON, energy=0.0)
    assert tired["较真"] < rested["较真"] and tired["幽默"] < rested["幽默"]
    assert psyche.sway(RULES.sway, CALM, "V1", NOON, close=1.0)["外向"] > rested["外向"]
    assert all(1 <= v <= 5 for v in tired.values())


def test_tensions_only_when_both_sides_are_strong():
    lonely_but_tired = psyche.tensions(RULES.ambivalence, {"social": 0.9}, 0.1, 0.0, CALM)
    assert lonely_but_tired[0][:2] == ("想找人热闹热闹", "又想一个人安静待着")
    assert not psyche.tensions(RULES.ambivalence, {"social": 0.9}, 0.9, 0.3, CALM)
    assert not psyche.tensions(RULES.ambivalence, {"social": 0.1}, 0.1, 0.0, CALM)


def test_each_temperament_digests_feelings_its_own_way():
    def how(**scores):
        return psyche.strategy(RULES.episode, CALM | scores, "V1")

    assert how(温和=5) == "reappraise"
    assert how(较真=5, 温和=1) == "ruminate"
    assert how(外向=5) == "share"
    assert how(外向=1) == "suppress"
    slow = RULES.episode.strategies["ruminate"].half_life
    assert slow > RULES.episode.strategies["reappraise"].half_life


def test_association_and_memory_cues():
    rules = RULES.assoc.model_copy(update={"chance": 1.0, "cue_memory": 1.0})
    key, hop = psyche.associate(rules, "外面下雨了", [], 3, random.Random(1))
    assert key == "雨" and hop
    assert psyche.associate(rules, "随便聊聊", [], 3, random.Random(1)) is None
    off = rules.model_copy(update={"chance": 0.0, "curious": 0.0})
    assert psyche.associate(off, "外面下雨了", [], 5, random.Random(1)) is None
    cue = psyche.cue_memory(rules, "我今天又去天台了", ["上次在天台看到流星"], random.Random(1))
    assert cue == ("天台", "上次在天台看到流星")
    # 常用词不算
    assert psyche.cue_memory(rules, "我们今天", ["我们今天"], random.Random(1)) is None


def test_values_stance_comes_from_temperament_and_profile():
    axis = RULES.values.axes[0]  # 随性自在 ↔ 有计划守规矩
    strict = psyche.stance(axis, CALM | {"较真": 5}, "", "V1")
    loose = psyche.stance(axis, CALM | {"较真": 1}, "", "V1")
    assert strict < 0 < loose
    free = psyche.stance(axis, CALM, "我最看重自由，随心", "V1")
    assert free > psyche.stance(axis, CALM, "", "V1")
    assert psyche.lean(axis, "做事就应该有计划") == -1
    assert psyche.lean(axis, "随便吧") == 1
    assert psyche.lean(axis, "今天吃什么") == 0


def test_disagreement_follows_a_consistent_stance():
    from roundtable.core.member.profile import ProfileStore

    def disagrees(values: str) -> int:
        env, brain = setup()
        ProfileStore(env.rt).change("V1", {"values": {"三观": values}}, author="user")
        last = "我觉得做事就应该有计划，按时来"
        cards = [
            brain.intent("V1", last=last, named=True, group=False, at=NOON, seed=str(i))
            for i in range(80)
        ]
        assert all(len(c.text) <= 220 for c in cards)
        return sum(c.intent == "disagree" for c in cards)

    # 看重自由随心的人更想反驳「应该有计划」，看重规矩的人认同
    assert disagrees("最看重自由，随心，无所谓") > disagrees("最看重规矩和计划，自律") + 5


def test_whim_can_be_blurted_and_counted():
    env, brain = setup((5, 3, 5, 3, 3))
    _with(env, whim=RULES.whim.model_copy(update={"chance": 1.0, "act_chance": 1.0}))
    cards = [
        brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed=str(i))
        for i in range(30)
    ]
    assert all("whim" in c.tags for c in cards)
    blurt = next(c for c in cards if c.intent == "blurt")
    assert blurt.spotlight.kind == "whim" and blurt.spotlight.text in blurt.text
    brain.after(blurt, NOON)
    snap = brain.snapshot("V1", NOON)
    assert snap["psy"]["whim"] == 1
    assert brain._load("V1").expects  # 说干就干：记成一个盼头


def test_letdown_leaves_an_episode_that_fades():
    env, brain = setup((1, 5, 3, 1, 3))  # 内向、较真、不太温和：反刍
    bus = EventBus(env.rt)
    s = brain._load("V1")
    s.goals = [{"name": "练琴", "xp": 3.5, "level": 0, "stuck": 0}]
    brain._anticipate_goal(s, s.goals[0], NOON)
    brain._save("V1", s)
    later = NOON + timedelta(days=3)
    brain.flush_signals("V1", bus, later)
    linger = brain._lingering("V1", brain._load("V1"), later + timedelta(hours=2))
    assert linger and linger[0]["feel"] == "有点失落" and linger[2] == "ruminate"
    _with(env, extra_lines=5, confab=RULES.confab.model_copy(update={"chance": 0.0}))
    card = brain.intent("V1", last="在干嘛", named=True, group=False, at=later, seed="e")
    assert "episode" in card.tags and "反复想" in card.text
    assert brain._lingering("V1", brain._load("V1"), later + timedelta(days=4)) is None


def test_tired_mood_is_misattributed_and_reasons_come_after():
    env, brain = setup()
    _with(
        env,
        extra_lines=5,
        confab=RULES.confab.model_copy(update={"chance": 1.0, "misattribute": 1.0}),
    )
    import roundtable.core.member.state as state_mod

    orig = state_mod.StatePlugin.read
    state_mod.StatePlugin.read = lambda self, m, at: type("R", (), {"mood": -0.6, "energy": 0.1})()
    try:
        card = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed="m")
    finally:
        state_mod.StatePlugin.read = orig
    assert "misattr" in card.tags and "其实是累了" in card.text
    assert "confab" in card.tags and "不一定是真原因" in card.text


def test_psyche_off_keeps_plain_cards():
    env, brain = setup()
    _with(env, enabled=False)
    card = brain.intent("V1", last="下雨了", named=True, group=False, at=NOON, seed="x")
    assert card.tags == ()


def test_same_rules_for_everyone():
    """换个成员 id、同样的档案：特质只随档案和 id 派生的随机数变，规则不认人。"""
    a = psyche.strategy(RULES.episode, CALM | {"温和": 5}, "V1")
    b = psyche.strategy(RULES.episode, CALM | {"温和": 5}, "Z9")
    assert a == b == "reappraise"
    when = datetime(2026, 11, 3, 9, tzinfo=UTC)
    assert psyche.tensions(RULES.ambivalence, {"mastery": 0.9}, 0.1, -0.5, CALM)
    assert Brain  # 只是确认模块能一起导入
    assert when
