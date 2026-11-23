"""斗地主 / 跑得快：叫地主、轮流出牌、别人都不要回到出牌人、有大必压、报单出最大、算分。"""

import random

import pytest

from roundtable.core.arcade import shed_bot
from roundtable.core.arcade.shedding import Doudizhu, Hand, Paodekuai, ShedError


def seats():
    return [Hand(f"p{i}", f"P{i}") for i in range(3)]


def play_out(g, rng):
    for _ in range(500):
        if g.done:
            return
        g.act(g.turn, shed_bot.decide(g, g.turn, rng))
    raise AssertionError("没打完")


def test_ddz_bidding_and_landlord_gets_bottom():
    g = Doudizhu(seats=seats(), rng=random.Random(3))
    g.start()
    assert g.phase == "bid" and all(len(s.cards) == 17 for s in g.seats)
    first = g.turn
    g.act(first, {"kind": "bid", "bid": 1})
    with pytest.raises(ShedError):
        g.act(g.turn, {"kind": "bid", "bid": 1})  # 要叫得更高
    g.act(g.turn, {"kind": "bid", "bid": 3})  # 叫 3 分直接当地主
    lord = g.landlord
    assert g.phase == "play" and g.turn == lord
    assert len(g.seats[lord].cards) == 20 and g.seats[lord].role == "地主"
    assert [o["kind"] for o in g.legal(lord)][0] == "play"  # 先出不能不要


def test_ddz_everyone_passes_redeals():
    g = Doudizhu(seats=seats(), rng=random.Random(4))
    g.start()
    for _ in range(3):
        g.act(g.turn, {"kind": "bid", "bid": 0})
    assert g.phase == "bid" and g.redeals == 1


def test_pass_returns_lead_and_illegal_play_rejected():
    g = Doudizhu(seats=seats(), rng=random.Random(5))
    g.start()
    g.act(g.turn, {"kind": "bid", "bid": 3})
    lord = g.turn
    low = g.seats[lord].cards[0]
    g.act(lord, {"kind": "play", "cards": [low]})
    with pytest.raises(ShedError):
        g.act(g.turn, {"kind": "play", "cards": ["XX"]})
    g.act(g.turn, {"kind": "pass"})
    g.act(g.turn, {"kind": "pass"})
    assert g.turn == lord and g.lead is None  # 别人都不要：地主接着出


def test_ddz_scores_sum_to_zero():
    rng = random.Random(6)
    g = Doudizhu(seats=seats(), rng=rng)
    for _ in range(5):
        g.start()
        play_out(g, rng)
        assert sum(r["points"] for r in g.results) == 0
        assert len(g.winners()) in (1, 2)


def test_pdk_spade_three_leads_and_must_beat():
    rng = random.Random(7)
    g = Paodekuai(seats=seats(), rng=rng)
    g.start()
    holder = next(i for i, s in enumerate(g.seats) if "3s" in s.cards)
    assert g.turn == holder and all(len(s.cards) == 16 for s in g.seats)
    assert all("3s" in o["cards"] for o in g.legal(holder))  # 第一手要带黑桃 3
    g.act(holder, {"kind": "play", "cards": ["3s"]})
    nxt = g.turn
    opts = g.legal(nxt)
    has_play = any(o["kind"] == "play" for o in opts)
    assert any(o["kind"] == "pass" for o in opts) != has_play  # 有大必压：能压就不能不要


def test_pdk_must_play_highest_single_when_next_has_one():
    g = Paodekuai(seats=seats(), rng=random.Random(8))
    g.start()
    g.seats[0].cards = ["4s", "9h", "Kd"]
    g.seats[1].cards = ["5s"]
    g.seats[2].cards = ["6s", "7s"]
    g.turn, g.lead, g.lead_by, g.history = 0, None, None, [{"kind": "pass"}]
    singles = [o for o in g.legal(0) if len(o["cards"]) == 1]
    assert [o["cards"] for o in singles] == [["Kd"]]
    with pytest.raises(ShedError):
        g.act(0, {"kind": "play", "cards": ["4s"]})


def test_pdk_scoring():
    rng = random.Random(9)
    g = Paodekuai(seats=seats(), rng=rng)
    for _ in range(5):
        g.start()
        play_out(g, rng)
        assert sum(r["points"] for r in g.results) == 0
        assert len(g.winners()) == 1


def test_view_hides_others_until_done():
    g = Doudizhu(seats=[Hand("you", "你"), Hand("a", "A"), Hand("b", "B")], rng=random.Random(2))
    g.start()
    v = g.view("you")
    assert v["seats"][0]["cards"] and v["seats"][1]["cards"] == []
    assert v["seats"][1]["count"] == 17
    assert v["bottom"] == []  # 地主定了才亮底牌


def test_pdk_winner_even_when_everyone_else_has_one_card():
    g = Paodekuai(seats=seats(), rng=random.Random(8))
    g.start()
    g.seats[0].cards = ["Kd"]
    g.seats[1].cards = ["5s"]
    g.seats[2].cards = ["6s"]
    g.turn, g.lead, g.lead_by = 0, None, None
    for s in g.seats:
        s.plays = 1
    g.act(0, {"kind": "play", "cards": ["Kd"]})
    assert g.done and g.winners() == ["p0"]
    assert all(r["points"] == 0 for r in g.results)


def test_tracker_and_notes():
    g = Doudizhu(seats=seats(), rng=random.Random(5))
    g.start()
    g.act(g.turn, {"kind": "bid", "bid": 3})
    lord = g.turn
    text = g.tracker(lord)
    assert "别人手里还有" in text and "炸弹" in text
    notes = [g.note_for(lord, o) for o in g.legal(lord)]
    assert all("出完还剩" in n or "能一手出完" in n for n in notes)
