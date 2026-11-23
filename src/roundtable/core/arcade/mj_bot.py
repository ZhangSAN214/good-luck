"""麻将的本地电脑对手（不调模型、不花钱）。

- 能胡就胡。
- 打牌：先挑打完离听牌最近的；一样近就看打完以后能让牌变好的张数（还没见光的）多的；
  再一样就先打字牌和边张。
  有人亮了三组以上、自己离听牌还远时，优先打别人打过的「安全牌」。
- 碰 / 明杠：碰完离听牌更近才碰；暗杠、补杠不影响听牌就杠。
"""

from __future__ import annotations

import random
from typing import Any

from roundtable.core.arcade.mahjong import INDEX, Mahjong, shanten


def _isolation(t: str, hand: list[str]) -> int:
    """越大越该先打：字牌单张 > 幺九单张 > 中张。"""
    if t[1] == "z":
        return 4 if hand.count(t) == 1 else 0
    n = int(t[0])
    near = sum(1 for d in (-2, -1, 1, 2) if 1 <= n + d <= 9 and f"{n + d}{t[1]}" in hand)
    score = 2 - min(2, near)
    if n in (1, 9):
        score += 1
    if hand.count(t) >= 2:
        score -= 2
    return score


def _ukeire(rest: list[str], melds: int, base: int, seen: Any) -> int:
    """打完之后，再摸到哪些牌能离听牌更近（还没见光的张数）。"""
    total = 0
    near = {t for t in rest if t[1] == "z"}
    for x in rest:
        if x[1] != "z":
            n = int(x[0])
            near.update(f"{n + d}{x[1]}" for d in (-2, -1, 0, 1, 2) if 1 <= n + d <= 9)
    for t in near:
        left = 4 - seen[t]
        if left <= 0:
            continue
        if shanten([*rest, t], melds) < base:
            total += left
    return total


def decide(game: Mahjong, i: int, rng: random.Random) -> dict[str, Any]:
    options = game.legal(i)
    if not options:
        raise ValueError("没轮到")
    hu = next((o for o in options if o["kind"] == "hu"), None)
    if hu is not None:
        return hu
    hand = game.seats[i].cards
    melds = len(game.melds[i])
    passing = next((o for o in options if o["kind"] == "pass"), None)
    if game.phase == "claim":
        now = shanten(hand, melds)
        for kind in ("kong", "pong"):
            o = next((x for x in options if x["kind"] == kind), None)
            if o is None:
                continue
            rest = list(hand)
            for t in o["cards"]:
                rest.remove(t)
            if kind == "kong":
                after = shanten(rest, melds + 1)
            else:
                after = min(
                    shanten([*rest[:k], *rest[k + 1 :]], melds + 1) for k in range(len(rest))
                )
            if after < now or (kind == "kong" and after <= now):
                return o
        return passing or options[-1]
    now = shanten(hand, melds)
    for o in options:
        if o["kind"] != "kong":
            continue
        rest = list(hand)
        for t in o["cards"]:
            rest.remove(t)
        if o["label"] == "补杠" or shanten(rest, melds + 1) <= now:
            return o
    plays = [o for o in options if o["kind"] == "play"]
    seen = game.seen(i)
    danger = any(len(game.melds[j]) >= 3 for j in range(4) if j != i)
    scored = []
    for o in plays:
        t = o["cards"][0]
        rest = list(hand)
        rest.remove(t)
        s = shanten(rest, melds)
        scored.append((s, o, rest))
    best = min(s for s, _, _ in scored)
    pool = [(o, rest) for s, o, rest in scored if s == best]
    if danger and best >= 2:
        safe = [
            (o, r)
            for o, r in pool
            if any(o["cards"][0] in game.rivers[j] for j in range(4) if j != i)
        ]
        if safe:
            pool = safe
    if len(pool) > 1 and best <= 2:
        return max(
            pool,
            key=lambda x: (
                _ukeire(x[1], melds, best, seen),
                _isolation(x[0]["cards"][0], hand),
                rng.random(),
            ),
        )[0]
    return max(
        pool,
        key=lambda x: (
            _isolation(x[0]["cards"][0], hand),
            -INDEX[x[0]["cards"][0]] % 9,
            rng.random(),
        ),
    )[0]
