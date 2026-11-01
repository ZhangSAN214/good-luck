"""防退化指标（成长 G3）：8 人相似度、长处喜好重合、走极端、被一个方向推、变太多、被你带偏、
口癖剩余、记忆重复；超过 chat.engine.growth.vitals 的阈值才报（大白话）。"""

from __future__ import annotations

from roundtable.core.member.growth import vitals as vt

from ...orchestrator.conftest import Env


def p(traits, likes=(), strengths=()):
    names = ("外向", "较真", "好奇", "温和", "幽默")
    return {
        "personality": {"五项": dict(zip(names, traits, strict=True)), "长处": list(strengths)},
        "likes": {"最爱": list(likes)},
    }


def test_spread_overlap_push_and_move():
    base = {"a": p((3, 3, 3, 3, 3), ["猫"]), "b": p((1, 5, 3, 3, 3), ["狗"])}
    now = {"a": p((4, 2, 3, 3, 3), ["猫", "奶茶"]), "b": p((2, 4, 3, 3, 3), ["狗", "奶茶"])}
    assert vt.spread(base) == {"min": 4, "mean": 4, "pair": ["a", "b"]}
    assert vt.spread(now)["min"] == 4
    rules = Env().rt.config.roundtable.chat.engine.growth
    assert vt.overlap(base, rules)["max"] == 0
    assert vt.overlap(now, rules)["max"] == round(1 / 3, 3)  # {猫,奶茶} 和 {狗,奶茶}
    assert vt.pushed(base, now) == {"外向": 1.0, "较真": -1.0, "好奇": 0, "温和": 0, "幽默": 0}
    assert vt.moved(base, now) == {"a": 2, "b": 2}


def blank(members=("a", "b")) -> dict:
    return {
        "spread": {"min": 6, "mean": 6, "pair": ["a", "b"]},
        "spread_start": {"min": 6, "mean": 6, "pair": ["a", "b"]},
        "overlap": {"max": 0.1, "mean": 0.1, "pair": ["a", "b"]},
        "overlap_start": {"max": 0.0, "mean": 0.0, "pair": []},
        "extreme": {"start": 0.1, "end": 0.1},
        "pushed": {"外向": 0.0},
        "moved": dict.fromkeys(members, 1),
        "user_share": {"members": {m: {"changes": 4, "user": 1} for m in members}},
        "lingo_left": {m: {"catchphrases": 1.0, "kaomoji": 4} for m in members},
        "memory_dupes": dict.fromkeys(members, 0.0),
    }


def test_problems_only_when_over_the_line():
    rt = Env().rt
    names = {"a": "阿一", "b": "阿二"}
    assert vt.problems(blank(), rt, names) == []
    v = blank()
    v["spread"] = {"min": 1, "mean": 3, "pair": ["a", "b"]}
    v["overlap"] = {"max": 0.6, "mean": 0.6, "pair": ["a", "b"]}
    v["extreme"] = {"start": 0.1, "end": 0.4}
    v["pushed"] = {"较真": -1.5}
    v["moved"]["a"] = 6
    v["user_share"]["members"]["a"] = {"changes": 12, "user": 8}
    v["lingo_left"]["b"] = {"catchphrases": 0.0, "kaomoji": 1}
    v["memory_dupes"]["b"] = 0.4
    got = "\n".join(vt.problems(v, rt, names))
    for want in (
        "性格撞型：阿一 和 阿二",
        "越长越像",
        "性格走极端",
        "被一个方向推：全员「较真」",
        "变太多：阿一",
        "被你带偏：阿一",
        "口头禅快用光：阿二",
        "颜文字快用光：阿二",
        "同一件事记了好几遍：阿二",
    ):
        assert want in got, want
    small = blank()
    small["user_share"]["members"]["a"] = {"changes": 3, "user": 3}  # 不到 10 条按 10 条算
    assert vt.problems(small, rt, names) == []
