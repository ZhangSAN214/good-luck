"""德州扑克比牌：7 张里挑最好的 5 张。返回可直接比较大小的元组（越大越好）。"""

from __future__ import annotations

from collections import Counter
from itertools import combinations

from roundtable.core.arcade.cards import rank_of, suit_of

CATEGORY_ZH = ["高牌", "一对", "两对", "三条", "顺子", "同花", "葫芦", "四条", "同花顺"]


def _straight_high(ranks: list[int]) -> int:
    """5 个不同点数是不是顺子；是则返回最大那张（A2345 算 5），否则 0。"""
    s = sorted(set(ranks))
    if len(s) != 5:
        return 0
    if s[-1] - s[0] == 4:
        return s[-1]
    if s == [2, 3, 4, 5, 14]:
        return 5
    return 0


def rank5(cards: tuple[str, ...] | list[str]) -> tuple[int, ...]:
    ranks = [rank_of(c) for c in cards]
    flush = len({suit_of(c) for c in cards}) == 1
    high = _straight_high(ranks)
    counts = Counter(ranks)
    # 先按张数、再按点数排：如葫芦 KKK22 → (13, 2)
    order = sorted(counts, key=lambda r: (counts[r], r), reverse=True)
    shape = sorted(counts.values(), reverse=True)
    if high and flush:
        return (8, high)
    if shape[0] == 4:
        return (7, *order)
    if shape[:2] == [3, 2]:
        return (6, *order)
    if flush:
        return (5, *sorted(ranks, reverse=True))
    if high:
        return (4, high)
    if shape[0] == 3:
        return (3, *order)
    if shape[:2] == [2, 2]:
        return (2, *order)
    if shape[0] == 2:
        return (1, *order)
    return (0, *sorted(ranks, reverse=True))


def best_hand(cards: list[str]) -> tuple[tuple[int, ...], tuple[str, ...]]:
    """5～7 张里最好的 5 张：(大小, 那 5 张)。"""
    best: tuple[tuple[int, ...], tuple[str, ...]] | None = None
    for combo in combinations(cards, 5):
        score = rank5(combo)
        if best is None or score > best[0]:
            best = (score, combo)
    assert best is not None
    return best


def hand_name(score: tuple[int, ...]) -> str:
    if score[0] == 8 and score[1] == 14:
        return "皇家同花顺"
    return CATEGORY_ZH[score[0]]


def _top_straight(ranks: set[int]) -> int:
    if 14 in ranks:
        ranks = ranks | {1}
    run = 0
    best = 0
    for r in range(1, 15):
        if r in ranks:
            run += 1
            if run >= 5:
                best = r
        else:
            run = 0
    return best


def score7(cards: list[str]) -> tuple[int, ...]:
    """快速比牌（电脑估胜率时一秒要算几万次）：结果和 best_hand(cards)[0] 一样。"""
    ranks = [rank_of(c) for c in cards]
    by_suit: dict[str, list[int]] = {}
    for c, r in zip(cards, ranks, strict=True):
        by_suit.setdefault(suit_of(c), []).append(r)
    flush = next((rs for rs in by_suit.values() if len(rs) >= 5), None)
    if flush is not None:
        sf = _top_straight(set(flush))
        if sf:
            return (8, sf)
    counts = Counter(ranks)
    order = sorted(counts, key=lambda r: (counts[r], r), reverse=True)
    quads = [r for r in order if counts[r] == 4]
    trips = [r for r in order if counts[r] == 3]
    pairs = [r for r in order if counts[r] == 2]
    if quads:
        kicker = max(r for r in ranks if r != quads[0])
        return (7, quads[0], kicker)
    if trips and (len(trips) > 1 or pairs):
        second = max([*trips[1:], *pairs])
        return (6, trips[0], second)
    if flush is not None:
        return (5, *sorted(flush, reverse=True)[:5])
    st = _top_straight(set(ranks))
    if st:
        return (4, st)
    if trips:
        kick = sorted((r for r in ranks if r != trips[0]), reverse=True)[:2]
        return (3, trips[0], *kick)
    if len(pairs) >= 2:
        hi, lo = sorted(pairs, reverse=True)[:2]
        kick = max(r for r in ranks if r not in (hi, lo))
        return (2, hi, lo, kick)
    if pairs:
        kick = sorted((r for r in ranks if r != pairs[0]), reverse=True)[:3]
        return (1, pairs[0], *kick)
    return (0, *sorted(ranks, reverse=True)[:5])
