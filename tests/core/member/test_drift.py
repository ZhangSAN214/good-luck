"""口癖和说话风格自己慢慢变：用腻了的颜文字、短回复换新的（只在自己的风格里挑、别人在用的不挑），
口头禅说腻了歇一阵，学来的用多了成了自己的，心情好坏用的颜文字不同。"""

from __future__ import annotations

import random
from datetime import timedelta

from roundtable.core.member.drift import Drift
from roundtable.core.member.lingo import Lingo
from roundtable.core.member.voice import member_voice

from .test_brain import NOON
from .test_lingo import NAMES, _lingo, card, world


def chatter(env, lingo: Lingo, member: str, word: str, days: int, per_day: int = 2) -> list:
    """她每天说几句带 word 的话（word 为空时用她现在的第一个颜文字），连着 days 天。返回变化。"""
    drift, changes = Drift(env.rt), []
    for d in range(days):
        for i in range(per_day):
            at = NOON + timedelta(days=d, hours=i)
            said = word or lingo.kaomoji(member)[0]
            changes += drift.count(member, f"今天也还行{said}", at, lingo)
            lingo._cache.clear()
    return changes


def eager(env) -> None:
    """测试用：用得勤就一定腻（概率 1）。"""
    rules = env.rt.config.roundtable.chat.engine.lingo.drift.model_copy(update={"chance": 1.0})
    _lingo(env, drift=rules)


def test_an_overused_kaomoji_is_swapped_for_a_new_one_in_her_own_style():
    env, lingo = world()
    card(env, "V1", (5, 3, 3, 5, 3))
    eager(env)
    lingo = Lingo(env.rt, names=NAMES)
    old = lingo.kaomoji("V1")[0]
    assert chatter(env, lingo, "V1", old, days=3) == []  # 才用了几天：不腻
    changes = chatter(env, lingo, "V1", old, days=16)
    swap = next(c for c in changes if c["kind"] == "kaomoji")
    assert swap["old"] == old and swap["new"] and "用腻了" in swap["why"]
    now = Lingo(env.rt, names=NAMES).kaomoji("V1")
    assert old not in now and swap["new"] in now
    rules = env.rt.config.roundtable.chat.engine.lingo
    groups = lingo._rank("V1", rules.kaomoji, "kaomoji")[: rules.drift.vibes_top]
    assert any(swap["new"] in rules.kaomoji[g].items for g in groups)  # 只在她自己的风格里挑
    others = {k for m in ("V2", "V3") for k in lingo.kaomoji(m)}
    assert swap["new"] not in others or len(others) >= 10  # 别人正在用的不挑


def test_curious_people_tire_sooner_than_stubborn_ones():
    def swaps(scores) -> int:
        env, _ = world()
        card(env, "V1", scores)
        rules = env.rt.config.roundtable.chat.engine.lingo.drift.model_copy(update={"min_days": 0})
        _lingo(env, drift=rules)  # 不等两周：看每天腻不腻的机会差多少
        lingo = Lingo(env.rt, names=NAMES)
        return sum(c["kind"] == "kaomoji" for c in chatter(env, lingo, "V1", "", days=30))

    curious, stubborn = swaps((3, 1, 5, 3, 3)), swaps((3, 5, 1, 3, 3))
    assert curious > stubborn and curious >= 1


def test_a_worn_catchphrase_rests_for_a_while_then_comes_back():
    env, lingo = world()
    card(env, "V1", style={"口头禅": ["这笔先记着", "问题不大"]})
    eager(env)
    lingo = Lingo(env.rt, names=NAMES)
    changes = chatter(env, lingo, "V1", "这笔先记着", days=16)
    rest = next(c for c in changes if c["kind"] == "rest")
    assert rest["old"] == "这笔先记着"
    later = NOON + timedelta(days=16)
    env.rt.budget.now = lambda: later
    assert "这笔先记着" not in member_voice(env.rt, "V1").catchphrases
    assert "问题不大" in member_voice(env.rt, "V1").catchphrases
    back = NOON + timedelta(days=60)
    env.rt.budget.now = lambda: back
    assert "这笔先记着" in member_voice(env.rt, "V1").catchphrases


def test_a_borrowed_phrase_used_enough_becomes_her_own_and_is_never_forgotten():
    env, lingo = world()
    card(env, "V1", style={"口头禅": ["问题不大"]})
    data = lingo._load("V1")
    data["learned"]["好家伙"] = {"from": "V2", "at": NOON.isoformat(), "used": ""}
    lingo._save("V1", data)
    changes = chatter(env, lingo, "V1", "好家伙", days=3)
    assert any(c["kind"] == "promote" and c["old"] == "好家伙" for c in changes)
    far = NOON + timedelta(days=200)
    assert "好家伙" in dict(lingo.learned("V1", far))  # 成了自己的：不会忘
    env.rt.budget.now = lambda: far
    assert "好家伙" in member_voice(env.rt, "V1").catchphrases


def test_her_face_follows_her_mood():
    env, lingo = world()
    card(env, "V1", (5, 3, 3, 3, 3))  # 外向：分到元气那组
    lingo = Lingo(env.rt, names=NAMES)
    drift = Drift(env.rt)
    up = drift.mood_face("V1", 0.6, lingo)
    assert up in lingo.kaomoji("V1")
    assert drift.mood_face("V1", 0.0, lingo) == ""
    rng = random.Random(0)
    line = lingo.card_line("V1", "", mood=0.6, energy=0.8, care=0.0, dislike=0.0, at=NOON, rng=rng)
    assert f"这会儿想用{up}" in line


def test_drift_can_be_switched_off():
    env, lingo = world()
    card(env, "V1")
    rules = env.rt.config.roundtable.chat.engine.lingo.drift.model_copy(
        update={"enabled": False, "chance": 1.0}
    )
    _lingo(env, drift=rules)
    lingo = Lingo(env.rt, names=NAMES)
    word = lingo.kaomoji("V1")[0]
    assert chatter(env, lingo, "V1", word, days=20) == []


def test_catchphrases_never_all_rest_at_once():
    """三句一起用得勤也不会一起歇：歇着的不超过一半（半年快进发现的）。"""
    env, lingo = world()
    phrases = ["这笔先记着", "问题不大", "稳了"]
    card(env, "V1", style={"口头禅": phrases})
    eager(env)
    lingo = Lingo(env.rt, names=NAMES)
    chatter(env, lingo, "V1", "".join(phrases), days=40)
    later = NOON + timedelta(days=40)
    rested = Drift(env.rt).resting("V1", later)
    assert len(rested) == 1  # 3 句里至少留 2 句（ceil(3 × 0.5)）


def test_swapped_faces_never_collapse_into_one():
    """一格换来的颜文字被再换掉时，只换那一格；太旧的记录并进底子，顺着找不断（半年快进发现）。"""
    from roundtable.core.member.drift import _fold

    swaps = [
        {"old": "A", "new": "Z"},  # A 那格 → Z
        {"old": "B", "new": "A"},  # B 那格 → A（A 本来的那格还是 Z）
        {"old": "A", "new": "W"},  # 现在显示成 A 的是 B 那格：只换它
    ]
    assert _fold({}, swaps) == {"A": "Z", "B": "W"}
    assert _fold(_fold({}, swaps[:2]), swaps[2:]) == _fold({}, swaps)  # 分两段叠结果一样
    env, _ = world()
    card(env, "V1", (5, 3, 3, 5, 3))
    rules = env.rt.config.roundtable.chat.engine.lingo.drift.model_copy(
        update={"chance": 1.0, "min_days": 0, "wear_uses": 2, "keep_swaps": 5, "return_days": 0}
    )
    _lingo(env, drift=rules)
    lingo = Lingo(env.rt, names=NAMES)
    start = len(lingo.kaomoji("V1"))
    chatter(env, lingo, "V1", "", days=120, per_day=3)
    data = Drift(env.rt)._load("V1")
    assert len(data["swaps"]) <= 5 and data["base"]  # 旧记录并进了底子
    now = Lingo(env.rt, names=NAMES).kaomoji("V1")
    assert len(now) == start  # 换了几十次，个数一个不少
