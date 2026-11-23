"""斗地主 / 跑得快的牌型：认牌型、比大小、找能出的组合（压得过的、不拆炸弹）。"""

import random

from roundtable.core.arcade.pattern import (
    beats,
    candidates,
    cards_zh,
    classify,
    ddz_deck,
    pdk_deck,
)


def kind(cards: str, game: str = "ddz", last: bool = False) -> str | None:
    p = classify(cards.split(), game, last=last)
    return p.kind if p else None


def test_ddz_kinds():
    assert kind("3s") == "single"
    assert kind("3s 3h") == "pair"
    assert kind("3s 3h 3d") == "triple"
    assert kind("3s 3h 3d 5c") == "triple1"
    assert kind("3s 3h 3d 5c 5d") == "triple2"
    assert kind("3s 4h 5d 6c 7s") == "straight"
    assert kind("Ts Jh Qd Kc As") == "straight"
    assert kind("Js Qh Kd Ac 2s") is None  # 2 不能连
    assert kind("3s 3h 4d 4c 5s 5h") == "pairs"
    assert kind("3s 3h 4d 4c") is None  # 斗地主连对至少三对
    assert kind("3s 3h 3d 4s 4h 4d") == "plane"
    assert kind("3s 3h 3d 4s 4h 4d 7c 9d") == "plane1"
    assert kind("3s 3h 3d 4s 4h 4d 7c 7d 9d 9s") == "plane2"
    assert kind("5s 5h 5d 5c 7d 9s") == "four2"
    assert kind("5s 5h 5d 5c") == "bomb"
    assert kind("BJ RJ") == "rocket"
    assert kind("3s 4h") is None


def test_pdk_kinds():
    assert kind("3s 3h 4d 4c", "pdk") == "pairs"  # 跑得快两对就能连
    assert kind("3s 3h 3d", "pdk") is None  # 三张只能最后出
    assert kind("3s 3h 3d", "pdk", last=True) == "triple"
    assert kind("3s 3h 3d 5c 9d", "pdk") == "triple2"  # 带任意两张
    assert kind("3s 3h 3d 4s 4h 4d 7c 8d 9d Ts", "pdk") == "planew"


def test_beats():
    c = lambda s: classify(s.split(), "ddz")  # noqa: E731
    assert beats(c("4s"), c("3s"))
    assert not beats(c("3h"), c("3s"))
    assert not beats(c("4s 4h"), c("3s"))
    assert beats(c("3s 3h 3d 3c"), c("2s 2h"))
    assert beats(c("4s 4h 4d 4c"), c("3s 3h 3d 3c"))
    assert beats(c("BJ RJ"), c("As Ah Ad Ac"))
    assert not beats(c("4s 5h 6d 7c 8s 9s"), c("3s 4h 5d 6c 7s"))  # 张数不同


def test_candidates_beat_previous():
    hand = ["3s", "4s", "5h", "6d", "7c", "9s", "9h", "9d", "9c", "Ks"]
    prev = classify(["8h"], "ddz")
    singles = [p for p in candidates(hand, "ddz", prev) if p.kind == "single"]
    assert {p.cards[0][0] for p in singles} == {"9", "K"}
    four2 = [p for p in candidates(hand, "ddz") if p.kind == "four2"]
    assert four2 and all(c[0] != "K" for c in four2[0].cards[4:])  # 带牌挑最小的
    assert any(p.kind == "bomb" for p in candidates(hand, "ddz", prev))
    leads = candidates(hand, "ddz")
    assert any(p.kind == "straight" for p in leads)


def test_decks():
    rng = random.Random(1)
    assert len(ddz_deck(rng)) == 54
    deck = pdk_deck(rng)
    assert len(deck) == 48 and "2s" in deck and "2h" not in deck and "As" not in deck
    assert cards_zh(["RJ", "Ts", "3h"]) == "♥3 ♠10 大王"
