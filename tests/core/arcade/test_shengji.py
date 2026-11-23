"""升级：主牌和大小、拆拖拉机、亮主、扣底、跟牌规矩、甩牌失败、毙牌、算分升级。"""

import random

import pytest

from roundtable.core.arcade import sj_bot
from roundtable.core.arcade.shedding import Hand, ShedError
from roundtable.core.arcade.shengji import Rules, Shengji, deck2, points_of


def table(seed: int = 1) -> Shengji:
    return Shengji(seats=[Hand(f"p{i}", f"P{i}") for i in range(4)], rng=random.Random(seed))


def to_play(g: Shengji, banker: int = 0, trump: str | None = "h") -> Shengji:
    """跳过亮主、扣底，直接到出牌。"""
    g.start()
    g.banker, g.trump, g.phase, g.turn = banker, trump, "play", banker
    g.kitty = []
    g._set_roles()
    return g


def test_deck_and_points():
    d = deck2(random.Random(1))
    assert len(d) == 108 and d.count("RJ") == 2
    assert points_of(d) == 200
    assert points_of(["5s", "Th", "Kd", "Ks", "BJ"]) == 35


def test_trump_order_and_tractors():
    r = Rules("2", "h")
    assert r.is_trump("2s") and r.is_trump("Ah") and not r.is_trump("As")
    order = [r.order(c) for c in ("Ah", "2s", "2h", "BJ", "RJ")]
    assert order == sorted(order) and len(set(order)) == 5
    assert r.order("2s") == r.order("2c")  # 副牌级牌一样大
    comps = r.decompose(["Ah", "Ah", "2s", "2s", "2h", "2h"])
    assert comps[0]["type"] == "tractor" and comps[0]["pairs"] == 3  # A♥ → 副级 → 主级 连得上
    # 级牌是 5：4 和 6 相邻
    r5 = Rules("5", "s")
    assert r5.decompose(["4d", "4d", "6d", "6d"])[0]["type"] == "tractor"
    assert [c["type"] for c in r.decompose(["2s", "2s", "2c", "2c"])] == ["pair", "pair"]


def test_declare_round_sets_trump_and_banker_takes_kitty():
    g = table(3)
    g.start()
    assert g.phase == "bid" and all(len(s.cards) == 25 for s in g.seats)
    for _ in range(4):
        g.act(g.turn, sj_bot.decide(g, g.turn, g.rng))
    assert g.phase == "bury" and g.banker is not None
    assert len(g.seats[g.banker].cards) == 33
    with pytest.raises(ShedError):
        g.act(g.banker, {"kind": "bury", "cards": g.seats[g.banker].cards[:7]})
    g.act(g.banker, g.legal(g.banker)[0])
    assert g.phase == "play" and len(g.kitty) == 8 and g.turn == g.banker


def test_later_declare_must_be_stronger():
    g = table(4)
    g.start()
    i = g.turn
    g.seats[i].cards[:2] = ["2s", "2s"]
    g.act(i, {"kind": "declare", "cards": ["2s"]})
    j = g.turn
    g.seats[j].cards[0] = "2h"
    with pytest.raises(ShedError):
        g.act(j, {"kind": "declare", "cards": ["2h"]})  # 一张压不过一张


def test_must_follow_suit_and_pairs():
    g = to_play(table(5))
    g.seats[0].cards = ["9s", "9s", "3d"]
    g.seats[1].cards = ["4s", "4s", "7s", "3c"]
    g.seats[2].cards = ["5c", "6c", "7c"]
    g.seats[3].cards = ["8d", "8d", "Ah"]
    g.act(0, {"kind": "play", "cards": ["9s", "9s"]})
    with pytest.raises(ShedError):
        g.act(1, {"kind": "play", "cards": ["4s", "7s"]})  # 有对子要跟对子
    with pytest.raises(ShedError):
        g.act(1, {"kind": "play", "cards": ["4s", "3c"]})
    g.act(1, {"kind": "play", "cards": ["4s", "4s"]})
    g.act(2, {"kind": "play", "cards": ["5c", "6c"]})  # 没有这一门：随便垫
    assert all(len(o["cards"]) == 2 for o in g.legal(3))
    assert not g.would_win(3, ["8d", "Ah"])  # 一张主牌不算毙：毙要和首家同牌型
    g.act(3, {"kind": "play", "cards": ["8d", "Ah"]})
    assert g.tricks == 1 and g.turn == 0  # 9 对最大


def test_ruff_with_trump_and_points_to_attackers():
    g = to_play(table(6))
    g.seats[0].cards = ["Ks", "3d"]
    g.seats[1].cards = ["3h", "4d"]
    g.seats[2].cards = ["5s", "5d"]
    g.seats[3].cards = ["As", "6d"]
    g.act(0, {"kind": "play", "cards": ["Ks"]})
    g.act(1, {"kind": "play", "cards": ["3h"]})  # 闲家用主牌毙
    g.act(2, {"kind": "play", "cards": ["5s"]})
    assert g.would_win(3, ["As"]) is False  # A♠ 管不过主牌
    g.act(3, {"kind": "play", "cards": ["As"]})
    assert g.turn == 1 and g.points == 15


def test_throw_fails_when_someone_can_beat_it():
    g = to_play(table(7))
    g.seats[0].cards = ["Ks", "Qs", "3d"]
    g.seats[1].cards = ["As", "4d", "4c"]
    g.seats[2].cards = ["5d", "6d", "7d"]
    g.seats[3].cards = ["8d", "9d", "Td"]
    g.act(0, {"kind": "play", "cards": ["Ks", "Qs"]})
    assert g.trick[0]["cards"] == ["Qs"] and "甩牌失败" in g.note  # 只能出最小的那张
    assert "Ks" in g.seats[0].cards


def test_full_rounds_levels_and_scores():
    g = table(8)
    for _ in range(6):
        g.start()
        while not g.done:
            g.act(g.turn, sj_bot.decide(g, g.turn, g.rng))
        assert sum(r["points"] for r in g.results) == 0
        assert len(g.winners()) == 2
        s = g.summary
        if s["attack_points"] < 80:
            assert s["winner"] == "庄家" and s["up"] >= 1
        else:
            assert s["winner"] == "闲家"


def test_view_and_brief_hide_others():
    g = Shengji(
        seats=[Hand("you", "你"), *[Hand(f"m{i}", f"M{i}") for i in range(3)]], rng=random.Random(2)
    )
    g.start()
    v = g.view("you")
    assert len(v["seats"][0]["cards"]) == 25 and v["seats"][1]["cards"] == []
    assert v["kitty"] == [] and v["trump_zh"] == "还没亮"
    b = g.brief(1)
    assert "对家是" in b["seat"] and "亮主" in b["last"]
