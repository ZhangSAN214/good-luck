"""出牌类游戏（斗地主、跑得快）的牌型：认牌型、比大小、从手牌里找出所有能出的组合。

牌写成两个字符：点数 + 花色（``3s`` ``Th`` ``2d``），大小王写 ``BJ``（小王）``RJ``（大王）。
大小：3 < 4 < … < K < A < 2 < 小王 < 大王。
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass
from itertools import combinations

ORDER = "3456789TJQKA2"
SUITS = "shdc"
SUIT_ZH = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
RANK_ZH = {"T": "10"}
TOP_CHAIN = 14  # 顺子、连对、飞机最大到 A（2 和王不能连）

KIND_ZH = {
    "single": "单张",
    "pair": "对子",
    "triple": "三张",
    "triple1": "三带一",
    "triple2": "三带二",
    "straight": "顺子",
    "pairs": "连对",
    "plane": "飞机",
    "plane1": "飞机带单",
    "plane2": "飞机带对",
    "planew": "飞机带翅膀",
    "four2": "四带二",
    "four22": "四带两对",
    "bomb": "炸弹",
    "rocket": "王炸",
}


def value(card: str) -> int:
    if card == "BJ":
        return 16
    if card == "RJ":
        return 17
    return ORDER.index(card[0]) + 3


def name_of(v: int) -> str:
    if v == 16:
        return "小王"
    if v == 17:
        return "大王"
    r = ORDER[v - 3]
    return RANK_ZH.get(r, r)


def card_zh(card: str) -> str:
    if card in ("BJ", "RJ"):
        return name_of(value(card))
    return SUIT_ZH[card[1]] + RANK_ZH.get(card[0], card[0])


def sort_cards(cards: list[str]) -> list[str]:
    return sorted(cards, key=lambda c: (value(c), c[-1]))


def cards_zh(cards: list[str]) -> str:
    return " ".join(card_zh(c) for c in sort_cards(cards)) or "（无）"


def ddz_deck(rng: random.Random) -> list[str]:
    deck = [r + s for r in ORDER for s in SUITS] + ["BJ", "RJ"]
    rng.shuffle(deck)
    return deck


def pdk_deck(rng: random.Random) -> list[str]:
    """跑得快 16 张一人：去掉大小王、三张 2（留黑桃 2）、一张 A（去掉黑桃 A），共 48 张。"""
    deck = [r + s for r in ORDER for s in SUITS]
    for c in ("2h", "2d", "2c", "As"):
        deck.remove(c)
    rng.shuffle(deck)
    return deck


@dataclass(frozen=True)
class Pattern:
    kind: str
    main: int  # 比大小看的点数（连的牌看最大那张）
    size: int  # 张数（同类型必须张数相同才能比）
    cards: tuple[str, ...]

    def label(self) -> str:
        if self.kind in ("straight", "pairs") or self.kind.startswith("plane"):
            per = {"straight": 1, "pairs": 2}.get(self.kind, 3)
            n = self._chain_len(per)
            lo = self.main - n + 1
            return f"{KIND_ZH[self.kind]} {name_of(lo)}～{name_of(self.main)}"
        if self.kind == "rocket":
            return "王炸"
        return f"{KIND_ZH[self.kind]} {name_of(self.main)}"

    def _chain_len(self, per: int) -> int:
        if self.kind in ("straight", "pairs", "plane"):
            return self.size // per
        wing = {"plane1": 4, "plane2": 5, "planew": 5}[self.kind]
        return max(2, self.size // wing)


def _run(values: list[int], need: int, length: int) -> int | None:
    """values 里能不能找到一段长 length 的连续点数（每个点数至少 need 张）；返回最高的那段的顶。"""
    counts = Counter(values)
    best = None
    for top in range(TOP_CHAIN, 3 + length - 2, -1):
        if all(counts.get(v, 0) >= need for v in range(top - length + 1, top + 1)):
            best = top
            break
    return best


def classify(cards: list[str], game: str, *, last: bool = False) -> Pattern | None:
    """认牌型；不是合法牌型返回 None。game 是 ``ddz``（斗地主）或 ``pdk``（跑得快）。
    last：这是出牌人手上最后几张（跑得快里三张、三带一只能最后出，飞机可以少带）。"""
    n = len(cards)
    if n == 0:
        return None
    vals = [value(c) for c in cards]
    counts = Counter(vals)
    shape = sorted(counts.values(), reverse=True)
    key = tuple(sort_cards(list(cards)))
    distinct = sorted(counts)

    def p(kind: str, main: int) -> Pattern:
        return Pattern(kind, main, n, key)

    if game == "ddz" and n == 2 and set(vals) == {16, 17}:
        return p("rocket", 17)
    if n == 4 and shape == [4]:
        return p("bomb", vals[0])
    if n == 1:
        return p("single", vals[0])
    if n == 2 and shape == [2]:
        return p("pair", vals[0])
    if n == 3 and shape == [3]:
        if game == "ddz" or last:
            return p("triple", vals[0])
        return None
    triple_v = [v for v, c in counts.items() if c >= 3]
    if n == 4 and shape[0] == 3 and (game == "ddz" or last):
        return p("triple1", triple_v[0])
    if n == 5 and shape[0] >= 3:
        t = max(triple_v)
        if game == "ddz" and shape == [3, 2]:
            return p("triple2", t)
        if game == "pdk":
            return p("triple2", t)
    # 顺子
    if n >= 5 and shape[0] == 1 and max(vals) <= TOP_CHAIN and distinct[-1] - distinct[0] == n - 1:
        return p("straight", distinct[-1])
    # 连对
    min_pairs = 3 if game == "ddz" else 2
    if (
        n % 2 == 0
        and n // 2 >= min_pairs
        and shape == [2] * (n // 2)
        and max(vals) <= TOP_CHAIN
        and distinct[-1] - distinct[0] == n // 2 - 1
    ):
        return p("pairs", distinct[-1])
    # 四带二
    if game == "ddz" and shape[0] == 4:
        four = next(v for v, c in counts.items() if c == 4)
        if n == 6:
            return p("four2", four)
        if n == 8 and sorted(c for v, c in counts.items() if v != four) == [2, 2]:
            return p("four22", four)
    # 飞机
    for k in range(n // 3, 1, -1):
        top = _run(vals, 3, k)
        if top is None:
            continue
        rest = Counter(vals)
        for v in range(top - k + 1, top + 1):
            rest[v] -= 3
        left = sum(rest.values())
        leftover = [c for c in rest.values() if c > 0]
        if left == 0:
            if game == "ddz" or last:
                return p("plane", top)
            continue
        if game == "ddz":
            if left == k and n == 4 * k:
                return p("plane1", top)
            if n == 5 * k and all(c in (2, 4) for c in leftover):
                return p("plane2", top)
        else:
            if n == 5 * k or (last and 3 * k < n < 5 * k):
                return p("planew", top)
    return None


def beats(new: Pattern, prev: Pattern) -> bool:
    if new.kind == "rocket":
        return True
    if prev.kind == "rocket":
        return False
    if new.kind == "bomb":
        return prev.kind != "bomb" or new.main > prev.main
    if prev.kind == "bomb":
        return False
    return new.kind == prev.kind and new.size == prev.size and new.main > prev.main


# --- 从手牌里找能出的 ---------------------------------------------------------------


def _pick(by: dict[int, list[str]], v: int, n: int) -> list[str]:
    return by[v][:n]


def candidates(hand: list[str], game: str, prev: Pattern | None = None) -> list[Pattern]:
    """手上所有能出的组合（压得过 prev 的；prev 为空时任意合法牌型）。带的牌挑最小的，不拆炸弹。"""
    by: dict[int, list[str]] = {}
    for c in sort_cards(hand):
        by.setdefault(value(c), []).append(c)
    vals = sorted(by)
    cnt = {v: len(by[v]) for v in vals}
    out: list[list[str]] = []

    def kickers(exclude: set[int], need: int, size: int) -> list[list[str]]:
        """挑 need 组、每组 size 张的带牌（尽量不拆炸弹，从小到大）。"""
        pool = [v for v in vals if v not in exclude and cnt[v] >= size]
        pool.sort(key=lambda v: (cnt[v] == 4, cnt[v] > size, v))
        if len(pool) < need:
            return []
        return [[c for v in pool[:need] for c in _pick(by, v, size)]]

    def loose(exclude: set[int], need: int) -> list[list[str]]:
        """跑得快：带任意 need 张（从最小的散牌挑）。"""
        rest = [c for v in vals if v not in exclude for c in by[v]]
        rest.sort(key=lambda c: (cnt[value(c)] == 4, cnt[value(c)], value(c)))
        return [rest[:need]] if len(rest) >= need else []

    for v in vals:
        out.append(_pick(by, v, 1))
        if cnt[v] >= 2:
            out.append(_pick(by, v, 2))
        if cnt[v] >= 3:
            t = _pick(by, v, 3)
            out.append(t)
            if game == "ddz":
                for k in kickers({v}, 1, 1):
                    out.append(t + k)
                for k in kickers({v}, 1, 2):
                    out.append(t + k)
            else:
                for k in loose({v}, 2):
                    out.append(t + k)
                for k in loose({v}, 1):
                    out.append(t + k)
        if cnt[v] == 4:
            four = _pick(by, v, 4)
            out.append(four)
            if game == "ddz":
                for k in kickers({v}, 2, 1):
                    out.append(four + k)
                for k in kickers({v}, 2, 2):
                    out.append(four + k)
    if game == "ddz" and 16 in by and 17 in by:
        out.append(["BJ", "RJ"])
    chain_vals = [v for v in vals if v <= TOP_CHAIN]
    for need, min_len in ((1, 5), (2, 3 if game == "ddz" else 2), (3, 2)):
        ok = [v for v in chain_vals if cnt[v] >= need]
        for i, start in enumerate(ok):
            run = [start]
            for v in ok[i + 1 :]:
                if v == run[-1] + 1:
                    run.append(v)
                else:
                    break
            for length in range(min_len, len(run) + 1):
                seg = run[:length]
                base = [c for v in seg for c in _pick(by, v, need)]
                if need < 3:
                    out.append(base)
                    continue
                out.append(base)
                if game == "ddz":
                    for k in kickers(set(seg), length, 1):
                        out.append(base + k)
                    for k in kickers(set(seg), length, 2):
                        out.append(base + k)
                else:
                    for k in loose(set(seg), 2 * length):
                        out.append(base + k)
    # 跑得快：最后几张能一把出完时（三张、三带一、飞机少带）
    last_cards = sort_cards(hand)
    seen: set[tuple[str, ...]] = set()
    result: list[Pattern] = []
    for cards in [*out, last_cards]:
        is_last = len(cards) == len(hand)
        pat = classify(cards, game, last=is_last)
        if pat is None or pat.cards in seen:
            continue
        if prev is not None and not beats(pat, prev):
            continue
        seen.add(pat.cards)
        result.append(pat)
    result.sort(
        key=lambda p: (p.kind in ("bomb", "rocket"), -p.size if prev is None else 0, p.main)
    )
    return result


def all_subsets_beat(hand: list[str], game: str, prev: Pattern) -> bool:
    """手上有没有能压过 prev 的（跑得快「有大必压」用；candidates 已覆盖常见情况）。"""
    if candidates(hand, game, prev):
        return True
    # 兜底：小手牌时穷举一遍
    if len(hand) <= 8:
        for n in range(1, len(hand) + 1):
            for combo in combinations(hand, n):
                pat = classify(list(combo), game, last=n == len(hand))
                if pat and beats(pat, prev):
                    return True
    return False
