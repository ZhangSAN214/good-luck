"""德州扑克的本地电脑对手（不调模型、不花钱）：估一下胜率，和底池赔率比，决定弃 / 跟 / 加。

成员的模型想不出来（超时、出错、到了每天上限）时也由它接手。每位成员的「性格」只是两个数：
敢不敢加注（aggression）和多松（looseness），由成员 id 和种子算出来，同一局里固定。
"""

from __future__ import annotations

import random
from typing import Any

from roundtable.core.arcade.cards import RANKS, SUITS
from roundtable.core.arcade.holdem import Holdem
from roundtable.core.arcade.poker_eval import score7

SIMS = 120


def equity(hole: list[str], board: list[str], opponents: int, rng: random.Random) -> float:
    """随机发完剩下的牌，算赢（平分算一半）的比例。"""
    if opponents <= 0:
        return 1.0
    known = set(hole) | set(board)
    deck = [r + s for r in RANKS for s in SUITS if r + s not in known]
    wins = 0.0
    for _ in range(SIMS):
        rng.shuffle(deck)
        k = 0
        others = []
        for _ in range(opponents):
            others.append(deck[k : k + 2])
            k += 2
        full = board + deck[k : k + 5 - len(board)]
        mine = score7(hole + full)
        best_other = max(score7(o + full) for o in others)
        if mine > best_other:
            wins += 1
        elif mine == best_other:
            wins += 0.5
    return wins / SIMS


def style(seat_id: str, seed: int) -> tuple[float, float]:
    r = random.Random(f"{seed}:{seat_id}")
    return (0.6 + r.random() * 0.8, -0.05 + r.random() * 0.12)  # (aggression, looseness)


def decide(game: Holdem, i: int, rng: random.Random, seed: int = 0) -> dict[str, Any]:
    """返回 legal() 里的一项。"""
    options = game.legal(i)
    if not options:
        raise ValueError("没轮到")
    s = game.seats[i]
    opponents = len(game.alive()) - 1
    eq = equity(s.hole, game.board, min(opponents, 4), rng)
    aggression, loose = style(s.id, seed)
    eq += loose
    need = game.to_call(i)
    pot = game.pot
    odds = need / (pot + need) if need else 0.0
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for o in options:
        by_kind.setdefault(o["kind"], []).append(o)
    fair = 1.0 / (opponents + 1)  # 平均下来的胜率
    strong = eq > fair + 0.25 * aggression and eq > 0.5
    good = eq > fair + 0.08
    bluff = rng.random() < 0.05 * aggression
    raises = by_kind.get("raise", [])
    if (strong or bluff) and raises:
        pick = raises[min(len(raises) - 1, 1 if eq > 0.75 else 0)]
        return pick
    if strong and "allin" in by_kind and eq > 0.8:
        return by_kind["allin"][0]
    if need == 0:
        if good and raises and rng.random() < 0.35 * aggression:
            return raises[0]
        return by_kind["check"][0]
    if eq >= odds + 0.03 or (good and need <= game.big_blind * 2):
        if "call" in by_kind:
            return by_kind["call"][0]
        if "allin" in by_kind and eq > 0.45:
            return by_kind["allin"][0]
    return by_kind["fold"][0]
