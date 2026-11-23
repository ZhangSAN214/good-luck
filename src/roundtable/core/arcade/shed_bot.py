"""斗地主 / 跑得快的本地电脑对手（不调模型、不花钱）。

会记牌（看别人手里还剩什么）、分得清队友和对手：
- 叫分看大牌和炸弹。
- 能一把出完就出完；手里只剩一手外面管不上的牌加一手别的，先出管不上的那手。
- 自己先出：队友（斗地主的农民）只剩一两张时，出最小的单张 / 对子放她走；
  对手只剩一张时不出单张（除非是外面最大的），只剩两张时不出对子。平时先甩长的连牌、从小往大出。
- 压牌：队友出的牌一般不压；对手快出完时用能压住的最大的牌拦；
  平时用最小的压得过的，不拆炸弹，炸弹留到对手快出完或自己快出完时再用。
"""

from __future__ import annotations

import random
from collections import Counter
from typing import Any

from roundtable.core.arcade.pattern import classify, value
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


def _main(o: dict[str, Any]) -> int:
    return max(value(c) for c in o["cards"])


def _breaks_bomb(o: dict[str, Any], hand: list[str]) -> bool:
    mine = Counter(value(c) for c in hand)
    used = Counter(value(c) for c in o["cards"])
    return not _is_big(o) and any(mine[v] == 4 and n < 4 for v, n in used.items())


def _boss(game: Shedding, i: int, o: dict[str, Any]) -> bool:
    """外面没人能用同样牌型管上（不算炸弹）。"""
    pat = classify(o["cards"], game.game, last=len(o["cards"]) == len(game.seats[i].cards))
    if pat is None or pat.kind not in ("single", "pair", "triple"):
        return False
    out = game.outside(i)
    return not any(n >= pat.size and v > pat.main for v, n in out.items())


def _enemies(game: Shedding, i: int) -> list[int]:
    return [j for j in range(len(game.seats)) if j != i and not game.teammate(i, j)]


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
    enemies = _enemies(game, i)
    low_enemy = min((len(game.seats[j].cards) for j in enemies), default=99)
    keep = [o for o in plays if not _breaks_bomb(o, me.cards)] or plays
    small = [o for o in keep if not _is_big(o)]
    leading = passing is None and (game.lead_by is None or game.lead_by == i)
    if leading:
        return _lead(game, i, small or keep, low_enemy, rng)
    # 压牌
    lead_by = game.lead_by
    if lead_by is not None and game.teammate(i, lead_by) and passing is not None:
        return passing  # 队友的牌不压
    danger = lead_by is not None and len(game.seats[lead_by].cards) <= 3
    if small:
        lord = isinstance(game, Doudizhu) and game.landlord == i
        if danger and not lord:  # 农民：地主快出完了，用外面管不上的最小的牌拦住，没有就出最大的
            boss = [o for o in small if _boss(game, i, o)]
            return min(boss, key=_main) if boss else max(small, key=_main)
        cheapest = min(small, key=_main)
        # 拿 2、王去压小牌不划算：对手不危险时看情况
        if passing is not None and _main(cheapest) >= 15 and low_enemy > 5 and rng.random() < 0.6:
            return passing
        return cheapest
    if danger or low_enemy <= 3 or len(me.cards) <= 6 or passing is None:
        return min(keep, key=_main)
    return passing


def _lead(
    game: Shedding, i: int, pool: list[dict[str, Any]], low_enemy: int, rng: random.Random
) -> dict[str, Any]:
    me = game.seats[i]
    # 只剩两手：先出外面管不上的那手
    bosses = [o for o in pool if _boss(game, i, o)]
    for o in bosses:
        rest = [c for c in me.cards]
        for c in o["cards"]:
            rest.remove(c)
        if rest and classify(rest, game.game, last=True) is not None:
            return o
    # 斗地主：队友只剩一两张，放她走
    if isinstance(game, Doudizhu) and game.landlord is not None and i != game.landlord:
        mate = next(j for j in range(3) if j not in (i, game.landlord))
        left = len(game.seats[mate].cards)
        if left in (1, 2):
            fit = [
                o
                for o in pool
                if len(o["cards"]) == left and classify(o["cards"], "ddz") is not None
            ]
            if fit:
                return min(fit, key=_main)
    safe = pool
    if low_enemy == 1:  # 对手只剩一张：别出单张（除非外面最大）
        safe = [o for o in pool if len(o["cards"]) > 1 or _boss(game, i, o)] or pool
    elif low_enemy == 2:
        safe = [o for o in pool if len(o["cards"]) != 2 or _boss(game, i, o)] or pool
    # 先甩张数多的（连牌、带牌），同样张数出小的；不把 2 和王单独先甩
    return max(safe, key=lambda o: (len(o["cards"]) >= 5, len(o["cards"]), -_main(o), rng.random()))
