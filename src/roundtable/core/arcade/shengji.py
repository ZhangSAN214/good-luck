"""升级（拖拉机）：四人两副牌，对家一队。纯逻辑。

一局：发牌（每人 25 张，底牌 8 张）→ 亮主（按座次轮一圈，单张级牌 < 一对级牌 < 一对小王 < 一对大王，
后亮的要比前面的大；没人亮就打无主）→ 庄家拿底、扣 8 张 → 出牌 25 轮 → 算分升级。

- 主牌：大王 > 小王 > 主花色的级牌 > 其他花色的级牌（一样大）> 主花色其余牌；
  无主时只有王和级牌是主。
- 首家可以出单张、对子、拖拉机（相邻的对子），或「甩牌」（同一门里几个别人都管不上的组合；
  有人管得上就算甩牌失败，只能出其中最小的那一组）。
- 跟牌：这一门够就必须跟这一门；首家出了对子 / 拖拉机，有对子要尽量跟对子、
  有同样长的拖拉机要跟拖拉机；
  这一门不够，先把这一门全出了再垫别的。没有这一门可以用主牌「毙」（牌型要和首家一样）。
- 分：5 = 5 分，10 和 K = 10 分，一共 200 分。闲家拿到 80 分换庄，每多 40 分多升一级；
  不到 80 分庄家升级（40～75 升 1 级，5～35 升 2 级，0 分升 3 级）。
  最后一轮闲家赢，底牌分翻倍（出对子翻 4 倍）。

选项和斗地主一样统一成列表：
``{"kind": "declare" / "pass" / "bury" / "play", "cards": [...], "label": ...}``。
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.arcade.shedding import Hand, Shedding, ShedError

RANKS = "23456789TJQKA"
SUITS = "shdc"
SUIT_ZH = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
RANK_ZH = {"T": "10"}
POINTS = {"5": 5, "T": 10, "K": 10}
JOKERS = ("BJ", "RJ")
HAND_SIZE = 25
KITTY = 8


def deck2(rng: random.Random) -> list[str]:
    deck = [r + s for r in RANKS for s in SUITS] * 2 + list(JOKERS) * 2
    rng.shuffle(deck)
    return deck


def points_of(cards: list[str]) -> int:
    return sum(POINTS.get(c[0], 0) for c in cards if c not in JOKERS)


def card_zh(c: str) -> str:
    if c == "BJ":
        return "小王"
    if c == "RJ":
        return "大王"
    return SUIT_ZH[c[1]] + RANK_ZH.get(c[0], c[0])


def rank_zh(r: str) -> str:
    return RANK_ZH.get(r, r)


@dataclass(frozen=True)
class Rules:
    """这一局的级牌和主花色（None = 无主）决定牌怎么分门、怎么比大小。"""

    level: str
    trump: str | None

    def is_trump(self, c: str) -> bool:
        return c in JOKERS or c[0] == self.level or (self.trump is not None and c[1] == self.trump)

    def klass(self, c: str) -> str:
        """门：主牌都算 ``T``，副牌按花色。"""
        return "T" if self.is_trump(c) else c[1]

    def order(self, c: str) -> int:
        """门内大小（相邻的数字才能连成拖拉机；副牌级牌都一样大）。"""
        plain = [r for r in RANKS if r != self.level]
        if not self.is_trump(c):
            return plain.index(c[0])
        base = len(plain) if self.trump is not None else 0
        if c == "RJ":
            return base + 3
        if c == "BJ":
            return base + 2
        if c[0] == self.level:
            return base + (1 if c[1] == self.trump else 0)
        return plain.index(c[0])  # 主花色的普通牌

    def sort(self, cards: list[str]) -> list[str]:
        """显示顺序：副牌按 ♠♥♣♦ 分门、门内从小到大，主牌放最后。"""
        group = {"s": 0, "h": 1, "c": 2, "d": 3, "T": 4}
        return sorted(cards, key=lambda c: (group[self.klass(c)], self.order(c), c[-1]))

    def zh(self, cards: list[str]) -> str:
        return " ".join(card_zh(c) for c in self.sort(cards)) or "（无）"

    # --- 组合 -------------------------------------------------------------------

    def decompose(self, cards: list[str]) -> list[dict[str, Any]]:
        """拆成拖拉机、对子、单张（同一门里）。

        每组：type / pairs（几对）/ top（最大那张的大小）/ cards。"""
        counts = Counter(cards)
        pairs = [c for c, n in counts.items() if n >= 2]
        singles = [c for c, n in counts.items() if n == 1]
        by_order: dict[int, list[str]] = {}
        for c in pairs:
            by_order.setdefault(self.order(c), []).append(c)
        comps: list[dict[str, Any]] = []
        chain: list[str] = []
        last = None
        for o in sorted(by_order):
            first, *rest = by_order[o]
            for extra in rest:  # 同样大的别的对子（副牌级牌）只能单独算对子
                comps.append(self._comp("pair", [extra]))
            if last is not None and o == last + 1 and chain:
                chain.append(first)
            else:
                if chain:
                    comps.append(self._comp("tractor" if len(chain) > 1 else "pair", chain))
                chain = [first]
            last = o
        if chain:
            comps.append(self._comp("tractor" if len(chain) > 1 else "pair", chain))
        for c in singles:
            comps.append({"type": "single", "pairs": 0, "top": self.order(c), "cards": [c]})
        comps.sort(key=lambda x: (-x["pairs"], -x["top"]))
        return comps

    def _comp(self, kind: str, pair_cards: list[str]) -> dict[str, Any]:
        cards = [c for c in pair_cards for _ in (0, 1)]
        return {
            "type": kind,
            "pairs": len(pair_cards),
            "top": max(self.order(c) for c in pair_cards),
            "cards": cards,
        }

    def signature(self, comps: list[dict[str, Any]]) -> tuple:
        tractors = tuple(
            sorted((c["pairs"] for c in comps if c["type"] == "tractor"), reverse=True)
        )
        npairs = sum(1 for c in comps if c["type"] == "pair")
        nsingle = sum(1 for c in comps if c["type"] == "single")
        return tractors, npairs, nsingle

    def chains(self, cards: list[str]) -> list[dict[str, Any]]:
        """手里这一门的所有对子 / 拖拉机（pairs ≥ 1）。"""
        return [c for c in self.decompose(cards) if c["pairs"] >= 1]

    def beaten_by(self, comp: dict[str, Any], others: list[str]) -> bool:
        """别人这一门的牌里有没有管得上 comp 的。"""
        if comp["type"] == "single":
            return any(self.order(c) > comp["top"] for c in others)
        need = comp["pairs"]
        for ch in self.chains(others):
            if (
                ch["pairs"] >= need
                and ch["top"] > comp["top"]
                and (need == 1 or ch["type"] == "tractor")
            ):
                return True
        return False


def comp_label(kind: str, pairs: int) -> str:
    if kind == "single":
        return "单张"
    if kind == "pair":
        return "对子"
    return f"拖拉机（{pairs} 对）"


@dataclass
class Shengji(Shedding):
    game: str = "sj"
    levels: list[str] = field(default_factory=lambda: ["2", "2"])  # 两队各自打到几
    banker: int | None = None
    trump: str | None = None
    kitty: list[str] = field(default_factory=list)
    declared: dict[str, Any] | None = None
    declare_turns: int = 0
    trick: list[dict[str, Any]] = field(default_factory=list)  # 这一轮已出的 {seat, cards}
    lead_comps: list[dict[str, Any]] = field(default_factory=list)
    lead_klass: str = ""
    last_trick: dict[str, Any] | None = None
    points: int = 0  # 闲家拿到的分
    tricks: int = 0
    note: str = ""
    summary: dict[str, Any] | None = None
    champion: list[int] = field(default_factory=list)  # 打过 A 的队（通关次数）

    # --- 基本 --------------------------------------------------------------------

    @property
    def level(self) -> str:
        team = self.banker % 2 if self.banker is not None else 0
        return self.levels[team]

    @property
    def rules(self) -> Rules:
        return Rules(self.level, self.trump)

    def team_of(self, i: int) -> int:
        return i % 2

    def is_banker_side(self, i: int) -> bool:
        return self.banker is not None and self.team_of(i) == self.team_of(self.banker)

    def start(self) -> None:
        if not self.can_start():
            raise ShedError("这一局还没打完")
        if len(self.seats) != 4:
            raise ShedError("升级要四个人")
        self.round_no += 1
        deck = deck2(self.rng)
        for k, s in enumerate(self.seats):
            s.cards = deck[k * HAND_SIZE : (k + 1) * HAND_SIZE]
            s.plays, s.last = 0, ""
        self.kitty = deck[4 * HAND_SIZE :]
        self.trump, self.declared, self.declare_turns = None, None, 0
        self.trick, self.lead_comps, self.lead_klass, self.last_trick = [], [], "", None
        self.points, self.tricks, self.note, self.summary = 0, 0, "", None
        self.results, self.history = [], []
        self.lead, self.lead_by = None, None
        self._sort_hands()
        self._set_roles()
        self.first = self.banker if self.banker is not None else self.rng.randrange(4)
        self.turn = self.first
        self.phase = "bid"

    def _sort_hands(self) -> None:
        r = self.rules
        for s in self.seats:
            s.cards = r.sort(s.cards)

    def _set_roles(self) -> None:
        for j, s in enumerate(self.seats):
            if self.banker is None:
                s.role = ""
            elif j == self.banker:
                s.role = "庄家"
            elif self.is_banker_side(j):
                s.role = "庄家对家"
            else:
                s.role = "闲家"

    # --- 合法动作 ------------------------------------------------------------------

    def legal(self, i: int) -> list[dict[str, Any]]:
        if self.turn != i:
            return []
        if self.phase == "bid":
            return self.declare_options(i)
        if self.phase == "bury":
            return [self._bury_hint(i)]
        if self.phase == "play":
            return self.play_options(i)
        return []

    def declare_options(self, i: int) -> list[dict[str, Any]]:
        have = Counter(self.seats[i].cards)
        cur = self.declared["strength"] if self.declared else 0
        opts: list[dict[str, Any]] = [{"kind": "pass", "label": "不亮"}]
        lv = self.level
        for s in SUITS:
            c = lv + s
            if have[c] >= 1 and cur < 1:
                opts.append(
                    {"kind": "declare", "cards": [c], "label": f"亮主 {SUIT_ZH[s]}（一张）"}
                )
            if have[c] >= 2 and cur < 2:
                opts.append(
                    {"kind": "declare", "cards": [c, c], "label": f"亮主 {SUIT_ZH[s]}（一对）"}
                )
        if have["BJ"] >= 2 and cur < 3:
            opts.append({"kind": "declare", "cards": ["BJ", "BJ"], "label": "打无主（一对小王）"})
        if have["RJ"] >= 2 and cur < 4:
            opts.append({"kind": "declare", "cards": ["RJ", "RJ"], "label": "打无主（一对大王）"})
        return opts

    def _bury_hint(self, i: int) -> dict[str, Any]:
        r = self.rules
        hand = self.seats[i].cards
        pairs = {c for c, n in Counter(hand).items() if n >= 2}
        ranked = sorted(
            hand,
            key=lambda c: (
                r.is_trump(c),
                c in pairs,
                POINTS.get(c[0], 0) if c not in JOKERS else 0,
                r.order(c),
            ),
        )
        return {"kind": "bury", "cards": ranked[:KITTY], "label": "扣底（建议：小的副牌）"}

    def play_options(self, i: int) -> list[dict[str, Any]]:
        if not self.trick:
            return self._lead_options(i)
        return self._follow_options(i)

    def _lead_options(self, i: int) -> list[dict[str, Any]]:
        r = self.rules
        hand = self.seats[i].cards
        by_class: dict[str, list[str]] = {}
        for c in hand:
            by_class.setdefault(r.klass(c), []).append(c)
        opts: list[dict[str, Any]] = []
        order = sorted(by_class, key=lambda k: (k == "T", k))
        for k in order:
            cards = by_class[k]
            comps = r.decompose(cards)
            for comp in sorted(comps, key=lambda x: (x["pairs"] == 0, -x["pairs"], x["top"])):
                if comp["type"] == "tractor":  # 拖拉机里的每一对也能单独出
                    opts.append(self._opt(comp["cards"], comp_label("tractor", comp["pairs"])))
                    for c in dict.fromkeys(comp["cards"]):
                        opts.append(self._opt([c, c], "对子"))
                elif comp["type"] == "pair":
                    opts.append(self._opt(comp["cards"], "对子"))
            for c in sorted(dict.fromkeys(cards), key=r.order):
                opts.append(self._opt([c], "单张"))
            if k != "T":
                safe = self._safe_throw(i, comps, k)
                if safe:
                    opts.insert(0, self._opt(safe, "甩牌"))
        seen: set[tuple[str, ...]] = set()
        out = []
        for o in opts:
            key = tuple(sorted(o["cards"]))
            if key not in seen:
                seen.add(key)
                out.append(o)
        return out

    def _safe_throw(self, i: int, comps: list[dict[str, Any]], k: str) -> list[str]:
        """这一门里别人都管不上的组合合起来（至少两组才算甩牌）。"""
        r = self.rules
        others = [c for j, s in enumerate(self.seats) if j != i for c in s.cards if r.klass(c) == k]
        safe = [comp for comp in comps if not r.beaten_by(comp, others)]
        if len(safe) < 2:
            return []
        return [c for comp in safe for c in comp["cards"]]

    def _opt(self, cards: list[str], kind: str) -> dict[str, Any]:
        r = self.rules
        cards = r.sort(cards)
        pts = points_of(cards)
        label = f"{kind} {r.zh(cards)}" + (f"（{pts} 分）" if pts else "")
        return {"kind": "play", "cards": cards, "label": label, "text": label}

    def _follow_options(self, i: int) -> list[dict[str, Any]]:
        r = self.rules
        hand = self.seats[i].cards
        n = len(self.trick[0]["cards"])
        k = self.lead_klass
        same = [c for c in hand if r.klass(c) == k]
        others = [c for c in hand if r.klass(c) != k]
        lead = self.lead_comps
        primary = lead[0]
        cands: list[list[str]] = []
        low = sorted(
            same, key=lambda c: (POINTS.get(c[0], 0) if c not in JOKERS else 0, r.order(c))
        )
        pts_first = sorted(
            same, key=lambda c: (-(POINTS.get(c[0], 0) if c not in JOKERS else 0), r.order(c))
        )
        if len(same) >= n:
            if len(lead) == 1 and primary["type"] == "single":
                cands += [[c] for c in dict.fromkeys(same)]
            elif len(lead) == 1:
                for ch in r.chains(same):
                    if ch["pairs"] >= primary["pairs"]:
                        cands += self._sub_chains(ch, primary["pairs"], primary["type"])
            for pool in (low, pts_first, sorted(same, key=r.order, reverse=True)):
                cands.append(self._fill(pool, same, n))
        else:
            rest_low = sorted(
                others,
                key=lambda c: (
                    r.is_trump(c),
                    POINTS.get(c[0], 0) if c not in JOKERS else 0,
                    r.order(c),
                ),
            )
            rest_pts = sorted(
                others,
                key=lambda c: (
                    r.is_trump(c),
                    -(POINTS.get(c[0], 0) if c not in JOKERS else 0),
                    r.order(c),
                ),
            )
            cands.append(same + rest_low[: n - len(same)])
            cands.append(same + rest_pts[: n - len(same)])
            if not same and k != "T":  # 没这一门：可以用主牌毙
                trumps = [c for c in hand if r.is_trump(c)]
                if len(lead) == 1 and primary["type"] == "single":
                    cands += [[c] for c in dict.fromkeys(trumps)]
                elif len(lead) == 1:
                    for ch in r.chains(trumps):
                        if ch["pairs"] >= primary["pairs"]:
                            cands += self._sub_chains(ch, primary["pairs"], primary["type"])
                else:
                    ruff = self._match_throw(trumps)
                    if ruff:
                        cands.append(ruff)
        seen: set[tuple[str, ...]] = set()
        out = []
        for cards in cands:
            key = tuple(sorted(cards))
            if len(cards) != n or key in seen:
                continue
            try:
                self._check_follow(i, cards)
            except ShedError:
                continue
            seen.add(key)
            kind = "毙" if k != "T" and cards and all(r.is_trump(c) for c in cards) else "跟"
            out.append(self._opt(cards, kind))
        return out

    def _sub_chains(self, ch: dict[str, Any], need: int, kind: str) -> list[list[str]]:
        r = self.rules
        uniq = sorted(dict.fromkeys(ch["cards"]), key=r.order)
        if kind == "pair":
            return [[c, c] for c in uniq]
        return [
            [c for c in uniq[s : s + need] for _ in (0, 1)] for s in range(len(uniq) - need + 1)
        ]

    def _match_throw(self, trumps: list[str]) -> list[str]:
        """用主牌凑出和甩牌一样的牌型（凑得出就能毙）。"""
        r = self.rules
        pool = list(trumps)
        picked: list[str] = []
        for comp in self.lead_comps:
            if comp["pairs"] == 0:
                continue
            chains = [ch for ch in r.chains(pool) if ch["pairs"] >= comp["pairs"]]
            if comp["type"] == "tractor":
                chains = [ch for ch in chains if ch["type"] == "tractor"]
            if not chains:
                return []
            ch = min(chains, key=lambda x: x["top"])
            got = self._sub_chains(ch, comp["pairs"], comp["type"])[0]
            for c in got:
                pool.remove(c)
            picked += got
        singles = sum(1 for comp in self.lead_comps if comp["pairs"] == 0)
        if len(pool) < singles:
            return []
        picked += sorted(pool, key=r.order)[:singles]
        return picked

    def _fill(self, pool: list[str], same: list[str], n: int) -> list[str]:
        """按首家的牌型要求先放对子 / 拖拉机，再用 pool 的顺序补齐。"""
        r = self.rules
        lead = self.lead_comps
        need_pairs = sum(c["pairs"] for c in lead)
        longest = max((c["pairs"] for c in lead if c["type"] == "tractor"), default=0)
        picked: list[str] = []
        left = list(same)
        if longest:
            tr = [ch for ch in r.chains(left) if ch["type"] == "tractor" and ch["pairs"] >= longest]
            if tr:
                rank = {c: pool.index(c) for c in pool}
                best = min(tr, key=lambda ch: min(rank[c] for c in ch["cards"]))
                got = self._sub_chains(best, longest, "tractor")[0]
                for c in got:
                    left.remove(c)
                picked += got
                need_pairs -= longest
        if need_pairs > 0:
            pairs = [c for c, m in Counter(left).items() if m >= 2]
            pairs.sort(key=pool.index)
            for c in pairs[:need_pairs]:
                left.remove(c)
                left.remove(c)
                picked += [c, c]
        for c in pool:
            if len(picked) >= n:
                break
            if c in left:
                left.remove(c)
                picked.append(c)
        return picked

    # --- 动作 ---------------------------------------------------------------------

    def act(self, i: int, option: dict[str, Any]) -> dict[str, Any]:
        if self.turn != i:
            raise ShedError("还没轮到")
        kind = option.get("kind")
        cards = list(option.get("cards") or [])
        if self.phase == "bid":
            if kind == "pass":
                return self._declare(i, None)
            if kind == "declare":
                return self._declare(i, cards)
            raise ShedError("现在是亮主")
        if self.phase == "bury":
            if kind != "bury":
                raise ShedError("现在要扣底")
            return self._bury(i, cards)
        if self.phase == "play":
            if kind != "play":
                raise ShedError("现在要出牌")
            return self._play_cards(i, cards)
        raise ShedError("这一局还没开始")

    def _declare(self, i: int, cards: list[str] | None) -> dict[str, Any]:
        s = self.seats[i]
        if cards is None:
            s.last = "不亮"
            entry = {"seat": s.id, "name": s.name, "kind": "pass", "label": "不亮"}
        else:
            match = next(
                (
                    o
                    for o in self.declare_options(i)
                    if o["kind"] == "declare" and sorted(o["cards"]) == sorted(cards)
                ),
                None,
            )
            if match is None:
                raise ShedError("亮不了这个（要有级牌，后亮的要比前面大）")
            strength = 4 if cards[0] == "RJ" else 3 if cards[0] == "BJ" else len(cards)
            self.declared = {
                "by": i,
                "cards": cards,
                "strength": strength,
                "suit": None if cards[0] in JOKERS else cards[0][1],
                "label": match["label"],
            }
            s.last = match["label"]
            entry = {"seat": s.id, "name": s.name, "kind": "declare", "label": match["label"]}
        self.history.append(entry)
        self.declare_turns += 1
        if self.declare_turns < 4:
            self.turn = self._nxt(i)
            return entry
        # 一圈亮完：定主、定庄，庄家拿底
        if self.declared:
            self.trump = self.declared["suit"]
            if self.banker is None:
                self.banker = self.declared["by"]
        else:
            self.trump = None
            if self.banker is None:
                self.banker = self.first
        self._set_roles()
        b = self.seats[self.banker]
        b.cards = b.cards + self.kitty
        self.kitty = []
        self._sort_hands()
        for o in self.seats:
            o.last = ""
        self.phase = "bury"
        self.turn = self.banker
        return entry

    def _bury(self, i: int, cards: list[str]) -> dict[str, Any]:
        s = self.seats[i]
        if len(cards) != KITTY:
            raise ShedError(f"要扣 {KITTY} 张")
        hand = list(s.cards)
        for c in cards:
            if c not in hand:
                raise ShedError("扣的牌不在手上")
            hand.remove(c)
        s.cards = hand
        self.kitty = list(cards)
        self.phase = "play"
        self.turn = i
        entry = {"seat": s.id, "name": s.name, "kind": "bury", "label": "扣好底牌"}
        self.history.append(entry)
        return entry

    def _play_cards(self, i: int, cards: list[str]) -> dict[str, Any]:
        r = self.rules
        s = self.seats[i]
        hand = list(s.cards)
        for c in cards:
            if c not in hand:
                raise ShedError("出的牌不在手上")
            hand.remove(c)
        if not cards:
            raise ShedError("要出牌")
        note = ""
        if not self.trick:
            ks = {r.klass(c) for c in cards}
            if len(ks) != 1:
                raise ShedError("首家出牌要是同一门（同一花色的副牌，或者都是主牌）")
            comps = r.decompose(cards)
            if len(comps) > 1:  # 甩牌：有人管得上就只能出最小的那一组
                k = next(iter(ks))
                others = [
                    c
                    for j, o in enumerate(self.seats)
                    if j != i
                    for c in o.cards
                    if r.klass(c) == k
                ]
                bad = [comp for comp in comps if r.beaten_by(comp, others)]
                if bad:
                    small = min(bad, key=lambda x: (x["pairs"], x["top"]))
                    cards = list(small["cards"])
                    hand = list(s.cards)
                    for c in cards:
                        hand.remove(c)
                    comps = [small]
                    note = f"{s.name}甩牌失败，只能出{comp_label(small['type'], small['pairs'])}"
            self.lead_comps = comps
            self.lead_klass = r.klass(cards[0])
        else:
            self._check_follow(i, cards)
        s.cards = hand
        s.plays += 1
        cards = r.sort(cards)
        s.last = r.zh(cards)
        self.trick.append({"seat": i, "cards": cards})
        self.note = note or (self.note if len(self.trick) > 1 else "")
        entry = {"seat": s.id, "name": s.name, "kind": "play", "cards": cards, "label": s.last}
        if len(self.trick) < 4:
            self.turn = self._nxt(i)
            self.lead_by = self.winning_index()
            return entry
        self._close_trick()
        return entry

    def _check_follow(self, i: int, cards: list[str]) -> None:
        r = self.rules
        hand = self.seats[i].cards
        n = len(self.trick[0]["cards"])
        if len(cards) != n:
            raise ShedError(f"要跟 {n} 张")
        left = list(hand)
        for c in cards:
            if c not in left:
                raise ShedError("出的牌不在手上")
            left.remove(c)
        k = self.lead_klass
        same = [c for c in hand if r.klass(c) == k]
        played_same = [c for c in cards if r.klass(c) == k]
        if len(same) < n:
            if len(played_same) != len(same):
                raise ShedError("这一门不够时，要先把这一门的牌全出了")
            return
        if len(played_same) != n:
            raise ShedError("这一门的牌够，要跟这一门")
        lead_pairs = sum(c["pairs"] for c in self.lead_comps)
        if lead_pairs:
            have_pairs = sum(1 for m in Counter(same).values() if m >= 2)
            got_pairs = sum(1 for m in Counter(cards).values() if m >= 2)
            if got_pairs < min(lead_pairs, have_pairs):
                raise ShedError("首家出了对子，你有对子要跟对子")
        longest = max((c["pairs"] for c in self.lead_comps if c["type"] == "tractor"), default=0)
        if longest:
            has = any(ch["type"] == "tractor" and ch["pairs"] >= longest for ch in r.chains(same))
            got = any(ch["type"] == "tractor" and ch["pairs"] >= longest for ch in r.chains(cards))
            if has and not got:
                raise ShedError("首家出了拖拉机，你有拖拉机要跟拖拉机")

    def _power(self, cards: list[str]) -> tuple[int, int] | None:
        """这一手能不能和首家比、比的话有多大：(是不是主, 最大那组的大小)。"""
        r = self.rules
        ks = {r.klass(c) for c in cards}
        if len(ks) != 1:
            return None
        k = next(iter(ks))
        if k != self.lead_klass and k != "T":
            return None
        comps = r.decompose(cards)
        if r.signature(comps) != r.signature(self.lead_comps):
            return None
        return (1 if k == "T" else 0, comps[0]["top"])

    def winning_index(self) -> int | None:
        if not self.trick:
            return None
        best = self.trick[0]
        best_p = self._power(best["cards"]) or (0, -1)
        for p in self.trick[1:]:
            pw = self._power(p["cards"])
            if pw is not None and pw > best_p:
                best, best_p = p, pw
        return best["seat"]

    def would_win(self, i: int, cards: list[str]) -> bool:
        if not self.trick:
            return False
        cur = self.winning_index()
        cur_p = self._power(next(p["cards"] for p in self.trick if p["seat"] == cur)) or (0, -1)
        pw = self._power(cards)
        return pw is not None and pw > cur_p

    def _close_trick(self) -> None:
        win = self.winning_index()
        assert win is not None
        pts = points_of([c for p in self.trick for c in p["cards"]])
        attackers = not self.is_banker_side(win)
        if attackers:
            self.points += pts
        self.tricks += 1
        self.last_trick = {
            "plays": [{"seat": self.seats[p["seat"]].id, "cards": p["cards"]} for p in self.trick],
            "winner": self.seats[win].id,
            "points": pts,
        }
        self.history.append(
            {
                "seat": self.seats[win].id,
                "name": self.seats[win].name,
                "kind": "trick",
                "label": f"第 {self.tricks} 轮 {self.seats[win].name} 大"
                + (f"，闲家得 {pts} 分" if attackers and pts else ""),
            }
        )
        lead_had_pairs = any(c["pairs"] for c in self.lead_comps)
        self.trick, self.lead_by = [], None
        for o in self.seats:
            o.last = ""
        if not any(o.cards for o in self.seats):
            bonus = 0
            if attackers:
                mult = 4 if lead_had_pairs else 2
                bonus = points_of(self.kitty) * mult
                self.points += bonus
            self._finish_round(bonus)
            return
        self.turn = win

    def _finish_round(self, bonus: int) -> None:
        assert self.banker is not None
        bt = self.team_of(self.banker)
        at = 1 - bt
        p = self.points
        if p < 80:
            up = 3 if p == 0 else 2 if p < 40 else 1
            win_team, nxt = bt, (self.banker + 2) % 4
        else:
            up = min(3, (p - 80) // 40)
            win_team, nxt = at, (self.banker + 1) % 4
        before = list(self.levels)
        idx = RANKS.index(self.levels[win_team]) + up
        passed = idx >= len(RANKS)
        if passed:  # 打过 A：这一队通关，两队从 2 重新打
            self.champion.append(win_team)
            self.levels = ["2", "2"]
        else:
            self.levels[win_team] = RANKS[idx]
        score = max(1, up)
        self.results = []
        for j, s in enumerate(self.seats):
            pts = score if self.team_of(j) == win_team else -score
            s.score += pts
            self.results.append(
                {"seat": s.id, "name": s.name, "role": s.role, "points": pts, "left": 0}
            )
        self.summary = {
            "winner": "庄家" if win_team == bt else "闲家",
            "attack_points": p,
            "kitty": list(self.kitty),
            "kitty_points": points_of(self.kitty),
            "bonus": bonus,
            "up": up,
            "levels_before": before,
            "levels": list(self.levels),
            "passed": passed,
            "next_banker": self.seats[nxt].name,
        }
        self.banker = nxt
        self.phase = "done"
        self.turn = None

    def winners(self) -> list[str]:
        return [r["seat"] for r in self.results if r["points"] > 0]

    # --- 给人看 / 给模型看 -----------------------------------------------------------

    def trump_zh(self) -> str:
        if self.phase == "bid" and self.declared is None:
            return "还没亮"
        suit = self.trump if self.phase != "bid" else (self.declared or {}).get("suit")
        return "无主" if suit is None else SUIT_ZH[suit]

    def view(self, viewer: str | None, *, reveal_all: bool = False) -> dict[str, Any]:
        r = self.rules
        you = next((i for i, s in enumerate(self.seats) if s.id == viewer), None)
        seats = []
        for i, s in enumerate(self.seats):
            show = reveal_all or s.id == viewer or self.done
            seats.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "count": len(s.cards),
                    "cards": r.sort(s.cards) if show else [],
                    "role": s.role,
                    "last": s.last,
                    "turn": i == self.turn,
                    "score": s.score,
                    "team": self.team_of(i),
                    "lead": i == self.lead_by,
                }
            )
        trick = [{"seat": self.seats[p["seat"]].id, "cards": p["cards"]} for p in self.trick]
        show_kitty = self.done or (
            you is not None and you == self.banker and self.phase in ("bury", "play")
        )
        return {
            "game": "shengji",
            "round_no": self.round_no,
            "phase": self.phase,
            "seats": seats,
            "options": self.legal(you) if you is not None else [],
            "results": list(self.results),
            "history": list(self.history[-24:]),
            "lead": None,
            "level": self.level,
            "levels": list(self.levels),
            "trump": self.trump if self.phase != "bid" else (self.declared or {}).get("suit"),
            "trump_zh": self.trump_zh(),
            "declared": {"by": self.seats[self.declared["by"]].id, "label": self.declared["label"]}
            if self.declared
            else None,
            "banker": self.seats[self.banker].id if self.banker is not None else None,
            "trick": trick,
            "winning": self.seats[self.lead_by].id if self.lead_by is not None else None,
            "last_trick": self.last_trick,
            "points": self.points,
            "kitty": r.sort(self.kitty) if show_kitty else [],
            "note": self.note,
            "summary": self.summary if self.done else None,
            "champion": list(self.champion),
        }

    def brief(self, i: int) -> dict[str, str]:
        r = self.rules
        s = self.seats[i]
        mate = self.seats[(i + 2) % 4].name
        if self.banker is None:
            seat = f"还没定庄。你的对家是{mate}，和你一队"
        else:
            side = "庄家方" if self.is_banker_side(i) else "闲家方"
            seat = f"你是{s.role}（{side}），对家是{mate}，和你一队"
        trump = self.trump_zh()
        seat += f"。这一局打 {rank_zh(self.level)}，主：{trump}"
        seat += f"；闲家已经拿了 {self.points} 分（80 分换庄）"
        lines = []
        for j, o in enumerate(self.seats):
            tag = "（你）" if j == i else "（对家）" if j == (i + 2) % 4 else ""
            lines.append(
                f"{o.name}{tag}{('，' + o.role) if o.role else ''}：还剩 {len(o.cards)} 张"
            )
        if self.phase == "bid":
            last = "现在是亮主：" + (
                f"{self.seats[self.declared['by']].name}已经{self.declared['label']}"
                if self.declared
                else "还没人亮"
            )
        elif self.phase == "bury":
            last = f"你是庄家，刚拿了底牌，要扣回 {KITTY} 张"
        elif not self.trick:
            last = "这一轮你先出"
        else:
            parts = [f"{self.seats[p['seat']].name}：{r.zh(p['cards'])}" for p in self.trick]
            w = self.winning_index()
            who = self.seats[w].name if w is not None else ""
            mine = (
                "（是你对家）"
                if w is not None and self.team_of(w) == self.team_of(i) and w != i
                else ""
            )
            last = "这一轮：" + "；".join(parts) + f"。现在最大的是{who}{mine}"
        recent = [h["label"] for h in self.history[-8:] if h.get("label")]
        return {
            "seat": seat,
            "hand": r.zh(s.cards),
            "table": "\n".join(lines),
            "last": last,
            "history": "\n".join(recent) or "（还没开始出牌）",
        }


__all__ = ["Hand", "Rules", "Shengji", "comp_label", "deck2", "points_of"]
