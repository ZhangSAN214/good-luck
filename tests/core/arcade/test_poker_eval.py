"""德州比牌：牌型判断、A2345 小顺、踢脚、7 选 5；快速算法和逐个组合算的结果一致。"""

import random

from roundtable.core.arcade.cards import card_zh, cards_zh, new_deck
from roundtable.core.arcade.poker_eval import best_hand, hand_name, rank5, score7


def name(cards: str) -> str:
    return hand_name(best_hand(cards.split())[0])


def test_categories():
    assert name("As Ks Qs Js Ts 2d 3c") == "皇家同花顺"
    assert name("9h 8h 7h 6h 5h Ad Ac") == "同花顺"
    assert name("9h 9d 9s 9c 5h Ad Kc") == "四条"
    assert name("9h 9d 9s 5c 5h Ad Kc") == "葫芦"
    assert name("2h 7h 9h Jh Kh Ad Ac") == "同花"
    assert name("5d 4c 3h 2s Ah Kd Kc") == "顺子"
    assert name("9h 9d 9s 5c 4h Ad Kc") == "三条"
    assert name("9h 9d 5s 5c 4h Ad Kc") == "两对"
    assert name("9h 9d 6s 5c 4h Ad Kc") == "一对"
    assert name("9h 2d 6s 5c 4h Ad Kc") == "高牌"


def test_wheel_is_lowest_straight_and_kickers_count():
    assert rank5(["Ah", "2d", "3c", "4s", "5h"]) < rank5(["2h", "3d", "4c", "5s", "6h"])
    assert rank5(["Ah", "Ad", "Kc", "7s", "2h"]) > rank5(["Ah", "Ad", "Qc", "7s", "2h"])


def test_fast_score_matches_brute_force():
    rng = random.Random(7)
    for _ in range(3000):
        cards = new_deck(rng)[:7]
        assert score7(cards) == best_hand(cards)[0]


def test_chinese_names():
    assert card_zh("Td") == "方块10"
    assert cards_zh(["As", "Kh"]) == "黑桃A 红心K"
    assert cards_zh([]) == "（无）"
