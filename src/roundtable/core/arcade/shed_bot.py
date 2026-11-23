"""斗地主 / 跑得快的本地电脑对手（不调模型、不花钱）。

叫分看大牌和炸弹；出牌：能一把出完就出完；自己先出时优先甩长的连牌、从小往大出；
压牌时用最小的压得过的，炸弹留到对手快出完或自己快出完时再用；斗地主里队友出的牌一般不压。
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from roundtable.core.arcade.pattern import value
from roundtable.core.arcade.shedding import Doudizhu, Shedding

BIG = ("炸弹", "王炸")


def hand_strength(cards: list[str]) -> float:
    vals = [value(c) for c in cards]
    counts = Counter(vals)
    score = 0.0
    score += 2.0 * counts.get(17, 0) + 1.5 * counts.get(16, 0)
    score += 1.0 * counts.get(15, 0) + 0.5 * counts.get(14, 0)
    score += 2.0 * sum(1 for c in counts.values() if c == 4)
    return score


def _is_big(o: dict[str, Any]) -> bool:
    return o.get("label", "").startswith(BIG)


def decide(game: Shedding, i: int, rng: random.Random) -> dict[str, Any]:
    options = game.legal(i)
    if not options:
        raise ValueError("没轮到")
    if game.phase == "bid":
        s = hand_strength(game.seats[i].cards) + rng.uniform(-0.5, 0.5)
        want = 3 if s >= 6 else 2 if s >= 4.5 else 1 if s >= 3 else 0
        bids = [o for o in options if o["bid"] <= want]
        return max(bids, key=lambda o: o["bid"])
    me = game.seats[i]
    plays = [o for o in options if o["kind"] == "play"]
    passing = next((o for o in options if o["kind"] == "pass"), None)
    for o in plays:  # 一把出完
        if len(o["cards"]) == len(me.cards):
            return o
    if not plays:
        return passing  # type: ignore[return-value]
    leading = passing is None and (game.lead_by is None or game.lead_by == i)
    small = [o for o in plays if not _is_big(o)]
    if leading:
        pool = small or plays
        # 先甩张数多的（连牌、带牌），同样张数出小的；不把 2 和王单独先甩
        return max(pool, key=lambda o: (len(o["cards"]) >= 5, len(o["cards"]), -_main(o)))
    # 压牌
    if isinstance(game, Doudizhu) and game.landlord is not None and game.lead_by is not None:
        lord = game.landlord
        mate = i != lord and game.lead_by != lord
        if mate and passing is not None:
            return passing  # 队友的牌不压
    danger = game.lead_by is not None and len(game.seats[game.lead_by].cards) <= 4
    if small:
        cheapest = min(small, key=_main)
        # 拿最大的牌去压小牌不划算：对手不危险时，压不压看牌力
        if passing is not None and _main(cheapest) >= 15 and not danger and rng.random() < 0.5:
            return passing
        return cheapest
    if danger or len(me.cards) <= 6 or passing is None:
        return min(plays, key=_main)
    return passing


def _main(o: dict[str, Any]) -> int:
    return max(value(c) for c in o["cards"])
