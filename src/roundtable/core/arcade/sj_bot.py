"""升级的本地电脑对手（不调模型、不花钱）。

亮主：有一对级牌就亮，一张级牌且那门牌多才亮；扣底用建议的小副牌；
出牌：先出能甩的、副牌的拖拉机和对子、副牌 A；对家正大就送分，
对手大时能管就用最小的管（这一轮有分或者快出完时才舍得用主毙），管不上就垫小的、不给分。
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from roundtable.core.arcade.shengji import JOKERS, POINTS, Shengji, points_of


def _pts(o: dict[str, Any]) -> int:
    return points_of(o["cards"])


def decide(game: Shengji, i: int, rng: random.Random) -> dict[str, Any]:
    options = game.legal(i)
    if not options:
        raise ValueError("没轮到")
    if game.phase == "bid":
        return _declare(game, i, options, rng)
    if game.phase == "bury":
        return options[0]
    r = game.rules
    if not game.trick:
        return _lead(game, options, rng)
    win = game.winning_index()
    mate_winning = win is not None and game.team_of(win) == game.team_of(i)
    if mate_winning:
        # 对家大：不去抢，尽量送分（但不拿主牌硬送）
        safe = [o for o in options if not game.would_win(i, o["cards"])] or options
        return max(safe, key=lambda o: (_pts(o), -sum(r.order(c) for c in o["cards"])))
    trick_pts = points_of([c for p in game.trick for c in p["cards"]])
    winners = [o for o in options if game.would_win(i, o["cards"])]
    if winners:
        cheapest = min(
            winners,
            key=lambda o: (
                all(r.is_trump(c) for c in o["cards"]),
                sum(r.order(c) for c in o["cards"]),
            ),
        )
        ruff = all(r.is_trump(c) for c in cheapest["cards"]) and game.lead_klass != "T"
        last = len(game.trick) == 3
        if (
            not ruff
            or trick_pts >= 10
            or last
            or len(game.seats[i].cards) <= 6
            or rng.random() < 0.3
        ):
            return cheapest
    return min(
        options,
        key=lambda o: (
            _pts(o),
            any(r.is_trump(c) for c in o["cards"]),
            sum(r.order(c) for c in o["cards"]),
        ),
    )


def _declare(
    game: Shengji, i: int, options: list[dict[str, Any]], rng: random.Random
) -> dict[str, Any]:
    hand = game.seats[i].cards
    nobody = game.declared is None and game.declare_turns == 3  # 最后一个人了还没人亮

    def length(o: dict[str, Any]) -> int:
        suit = o["cards"][0][1]
        return sum(1 for c in hand if c not in JOKERS and c[1] == suit)

    suits = [o for o in options if o["kind"] == "declare" and o["cards"][0] not in JOKERS]
    jokers = [o for o in options if o["kind"] == "declare" and o["cards"][0] in JOKERS]
    if suits:
        best = max(suits, key=lambda o: (len(o["cards"]), length(o)))
        n = length(best)
        if (
            nobody
            or n >= 6
            or (len(best["cards"]) == 2 and n >= 4)
            or (n >= 4 and rng.random() < 0.5)
        ):
            return best
    if jokers and (nobody or rng.random() < 0.3):
        return jokers[-1]
    return options[0]


def _lead(game: Shengji, options: list[dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    r = game.rules
    throws = [o for o in options if o["label"].startswith("甩牌")]
    if throws:
        return max(throws, key=lambda o: len(o["cards"]))
    side = [o for o in options if not r.is_trump(o["cards"][0])]
    multi = [o for o in side if len(o["cards"]) >= 2]
    if multi:
        return max(multi, key=lambda o: (len(o["cards"]), max(r.order(c) for c in o["cards"])))
    top = len([x for x in "23456789TJQKA" if x != game.level]) - 1
    aces = [o for o in side if r.order(o["cards"][0]) == top]
    if aces:
        return aces[0]
    pool = side or options
    counts = Counter(r.klass(o["cards"][0]) for o in pool)
    # 出短门的小牌（好早点断门去毙），不出分
    return min(
        pool,
        key=lambda o: (
            POINTS.get(o["cards"][0][0], 0) if o["cards"][0] not in JOKERS else 0,
            counts[r.klass(o["cards"][0])],
            r.order(o["cards"][0]),
            rng.random(),
        ),
    )
