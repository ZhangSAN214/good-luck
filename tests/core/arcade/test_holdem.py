"""德州规则：盲注、轮到谁、过牌 / 跟注 / 加注的合法性、边池、全下发完、筹码守恒。"""

import random

import pytest

from roundtable.core.arcade import holdem_bot
from roundtable.core.arcade.holdem import Holdem, HoldemError, Seat


def table(*stacks: int, seed: int = 1) -> Holdem:
    seats = [Seat(id=f"p{i}", name=f"P{i}", stack=s) for i, s in enumerate(stacks)]
    return Holdem(seats=seats, rng=random.Random(seed))


def test_blinds_and_first_to_act():
    g = table(1000, 1000, 1000)
    g.start_hand()
    assert g.dealer == 0
    assert [s.bet for s in g.seats] == [0, 10, 20]
    assert g.turn == 0  # 大盲左手边
    assert all(len(s.hole) == 2 for s in g.seats)
    kinds = [o["kind"] for o in g.legal(0)]
    assert kinds[:2] == ["fold", "call"] and kinds[-1] == "allin"


def test_heads_up_dealer_posts_small_blind_and_acts_first():
    g = table(1000, 1000)
    g.start_hand()
    assert g.seats[g.dealer].bet == 10
    assert g.turn == g.dealer


def test_illegal_moves_rejected():
    g = table(1000, 1000, 1000)
    g.start_hand()
    with pytest.raises(HoldemError):
        g.act(1, "call")  # 没轮到
    with pytest.raises(HoldemError):
        g.act(0, "check")  # 前面有大盲
    with pytest.raises(HoldemError):
        g.act(0, "raise", 25)  # 最少加到 40
    with pytest.raises(HoldemError):
        g.act(0, "raise", 5000)


def test_big_blind_gets_option_then_streets_advance():
    g = table(1000, 1000, 1000)
    g.start_hand()
    g.act(0, "call")
    g.act(1, "call")
    assert g.street == "preflop" and g.turn == 2  # 大盲还能加
    assert [o["kind"] for o in g.legal(2)][0] == "check"
    g.act(2, "check")
    assert g.street == "flop" and len(g.board) == 3
    assert g.turn == 1  # 翻牌后从庄家左手边开始
    g.act(1, "check")
    g.act(2, "raise", 40)
    g.act(0, "fold")
    g.act(1, "call")
    assert g.street == "turn" and len(g.board) == 4


def test_everyone_folds_to_one_winner():
    g = table(1000, 1000, 1000)
    g.start_hand()
    g.act(0, "fold")
    g.act(1, "fold")
    assert g.street == "done"
    assert g.winners() == ["p2"]
    assert g.seats[2].stack == 1010


def test_side_pot_and_run_out():
    g = table(100, 1000, 1000, seed=3)
    g.start_hand()
    g.act(0, "allin")
    g.act(1, "call")
    g.act(2, "call")
    # 两位还有钱的接着下注；短码只能赢主池
    while g.street != "done":
        g.act(g.turn, "check" if g.legal(g.turn)[0]["kind"] == "check" else "call")
    assert sum(s.stack for s in g.seats) == 2100
    short = next(r for r in g.results if r["seat"] == "p0")
    assert short["won"] <= 300  # 短码最多赢走主池（3 × 100）


def test_all_in_preflop_deals_whole_board():
    g = table(500, 500)
    g.start_hand()
    g.act(g.turn, "allin")
    g.act(g.turn, "allin")
    assert g.street == "done" and len(g.board) == 5
    assert sum(s.stack for s in g.seats) == 1000


def test_busted_player_sits_out():
    g = table(1000, 1000, 0)
    g.start_hand()
    assert g.seats[2].out and g.seats[2].hole == []
    assert not table(1000, 0).can_start()


def test_view_hides_other_hole_cards():
    seats = [Seat("you", "你", 1000), Seat("a", "A", 1000)]
    g = Holdem(seats=seats, rng=random.Random(2))
    g.start_hand()
    v = g.view()
    assert v["seats"][0]["hole"][0] != "??"
    assert v["seats"][1]["hole"] == ["??", "??"]
    assert "你" in g.brief(1)["table"] or "A（你）" in g.brief(1)["table"]


def test_bots_play_many_hands_and_chips_are_conserved():
    rng = random.Random(5)
    g = table(300, 1000, 50, 1000, 600, seed=5)
    total = sum(s.stack for s in g.seats)
    hands = 0
    while g.can_start() and hands < 12:
        g.start_hand()
        hands += 1
        for _ in range(300):
            if g.street == "done":
                break
            move = holdem_bot.decide(g, g.turn, rng, 5)
            g.act(g.turn, move["kind"], move.get("to"))
        assert g.street == "done"
        assert sum(s.stack for s in g.seats) == total
