"""出牌类游戏（斗地主、跑得快）的手牌结构估计：这手牌最少还要出几手、零碎小牌多不多、有几张控制牌。

本地电脑对手用它挑出法：同样能出，挑出完以后手牌最整齐（手数少、零碎小牌少）的那一种，
不为了压一张单牌拆掉对子、顺子、三张、炸弹。纯计算，不调模型。
"""

from __future__ import annotations

from collections import Counter
from functools import lru_cache

from roundtable.core.arcade.pattern import TOP_CHAIN, value

LOW = 11  # J 以下算小牌：单出的小单张、小对子最难出手


def shape_score(cards: list[str], game: str) -> float:
    """越小越好：约等于还要出几手，再加上零碎小牌的负担、减去控制牌（2、王、炸弹）。"""
    counts = Counter(value(c) for c in cards)
    return _score(tuple(counts.get(v, 0) for v in range(3, 18)), game)


@lru_cache(maxsize=200_000)
def _score(counts: tuple[int, ...], game: str) -> float:
    best = _base(counts, game)
    # 试着抽出顺子（≥5 张单）和连对（斗地主 ≥3 对，跑得快 ≥2 对），递归看剩下的
    c = list(counts)
    top = TOP_CHAIN - 3  # 下标：A
    for need, min_len in ((1, 5), (2, 3 if game == "ddz" else 2)):
        for lo in range(0, top + 1):
            hi = lo
            while hi <= top and c[hi] >= need:
                hi += 1
            # lo..hi-1 是一段够格的；试每个够长的子段（从 lo 开始），避免重复只从 lo 起
            for end in range(lo + min_len, hi + 1):
                rest = list(c)
                for v in range(lo, end):
                    rest[v] -= need
                best = min(best, 1 + _score(tuple(rest), game))
    return best


def _base(counts: tuple[int, ...], game: str) -> float:
    singles = pairs = triples = bombs = 0
    low_singles = low_pairs = 0
    for k, n in enumerate(counts):
        v = k + 3
        if n == 4:
            bombs += 1
        elif n == 3:
            triples += 1
        elif n == 2:
            pairs += 1
            low_pairs += v < LOW
        elif n == 1:
            singles += 1
            low_singles += v < LOW
    rocket = game == "ddz" and counts[13] == 1 and counts[14] == 1  # 小王、大王
    if rocket:
        singles -= 2
        bombs += 1
    # 三张带走零碎：斗地主带一张或一对，跑得快带两张
    carry = triples
    if game == "ddz":
        take_s = min(carry, low_singles)
        carry -= take_s
        take_p = min(carry, low_pairs)
    else:
        take_s = min(2 * carry, low_singles) // 2 * 2
        carry -= take_s // 2
        take_p = min(carry, low_pairs)
    singles -= take_s
    low_singles -= take_s
    pairs -= take_p
    low_pairs -= take_p
    hands = singles + pairs + triples + bombs
    controls = counts[12] + counts[13] + counts[14] + 2 * bombs  # 2、小王、大王、炸弹
    return hands + 0.35 * low_singles + 0.2 * low_pairs - 0.3 * controls
