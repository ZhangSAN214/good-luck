"""出牌类手牌结构估计（core/arcade/hand_shape.py）和用它挑出法的本地电脑：不为压一张单牌拆整手牌。"""

from __future__ import annotations

import random

from roundtable.core.arcade import shed_bot
from roundtable.core.arcade.hand_shape import shape_score
from roundtable.core.arcade.shedding import Doudizhu, Hand


def test_shape_counts_hands_and_prefers_whole_sets():
    straight = ["3s", "4h", "5d", "6c", "7s"]
    assert shape_score(straight, "ddz") == 1.0  # 一手顺子
    # 顺子多一张 7：拆成两手，还多一张零碎
    assert shape_score([*straight, "7h"], "ddz") > shape_score(straight, "ddz")
    # 三张带一对：一手
    assert shape_score(["9s", "9h", "9d", "3s", "3h"], "ddz") < shape_score(
        ["9s", "9h", "3s", "3h", "Kd"], "ddz"
    )
    # 2 和王是控制牌，手里有反而好
    assert shape_score(["2s", "3h"], "ddz") < shape_score(["Ks", "3h"], "ddz")


def test_bot_does_not_break_a_pair_to_beat_a_single():
    g = Doudizhu(seats=[Hand(f"p{k}", f"P{k}") for k in range(3)], rng=random.Random(1))
    g.start()
    g.phase = "play"
    g.landlord = 0
    g.seats[0].cards = ["8s", "8h", "Ts", "Th", "Qs", "Qh", "Kd", "3c", "3d", "5c", "5d"]
    g.seats[1].cards = ["3s", "4s", "5s", "6s", "9d", "9c", "Jd", "Jc", "Ad", "Ac"]
    g.seats[2].cards = ["3h", "4h", "5h", "6h", "9h", "9s", "Jh", "Js", "Ah", "As"]
    # 农民出了一张 7，地主手里能压的有 8、10、Q（都成对）和单张 K：压 K 最划算
    g.lead, g.lead_by, g.turn, g.passes = None, 1, 0, 0
    from roundtable.core.arcade.pattern import classify

    g.lead = classify(["7d"], "ddz")
    move = shed_bot.decide(g, 0, random.Random(2))
    assert move["kind"] == "play" and move["cards"] == ["Kd"]


def test_holdem_bot_respects_raises_and_sizes_bets():
    from roundtable.core.arcade import holdem_bot

    # 对手加过注时，同一手中等牌的胜率估得更低（她的牌多半不差）
    loose = holdem_bot.equity(["Kd", "9c"], [], 1, random.Random(1))
    tight = holdem_bot.equity(["Kd", "9c"], [], 1, random.Random(1), tight=0.5)
    assert tight < loose - 0.05
    assert holdem_bot.preflop_strength(["As", "Ah"]) > holdem_bot.preflop_strength(["7s", "2d"])
    raises = [{"kind": "raise", "to": t} for t in (40, 70, 120)]
    assert holdem_bot._sized(raises, 75) == {"kind": "raise", "to": 70}
