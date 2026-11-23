"""出牌类游戏：斗地主（叫分 1～3，地主对两个农民）和跑得快（16 张，有大必压，报单出最大）。纯逻辑。

三个座位，谁先出完谁赢。每一步的「能做的事」统一成选项列表：
``{"kind": "bid", "bid": 0～3}``、``{"kind": "pass"}``、
``{"kind": "play", "cards": [...], "label": "对子 5"}``。
"""

from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.arcade.pattern import (
    KIND_ZH,
    Pattern,
    beats,
    candidates,
    cards_zh,
    classify,
    ddz_deck,
    name_of,
    pdk_deck,
    sort_cards,
    value,
)

MAX_OPTIONS = 40  # 给成员看的选项最多几项（太多了模型看不过来）


class ShedError(ValueError):
    """不合规则的操作。"""


@dataclass
class Hand:
    id: str
    name: str
    cards: list[str] = field(default_factory=list)
    role: str = ""  # 斗地主：地主 / 农民
    plays: int = 0  # 出过几手
    last: str = ""  # 上一个动作（显示在座位上）
    score: int = 0  # 这一桌累计得分


@dataclass
class Shedding:
    """斗地主和跑得快共用的部分。"""

    seats: list[Hand]
    rng: random.Random = field(default_factory=random.Random)
    game: str = "ddz"
    round_no: int = 0
    phase: str = "idle"  # idle / bid / play / done
    turn: int | None = None
    lead: Pattern | None = None  # 现在要压的那一手
    lead_by: int | None = None
    passes: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    first: int = 0  # 这一局谁先

    # --- 公共 --------------------------------------------------------------------

    def index_of(self, seat_id: str) -> int:
        for i, s in enumerate(self.seats):
            if s.id == seat_id:
                return i
        raise ShedError("没有这个座位")

    @property
    def done(self) -> bool:
        return self.phase == "done"

    def can_start(self) -> bool:
        return self.phase in ("idle", "done")

    def _nxt(self, i: int) -> int:
        return (i + 1) % len(self.seats)

    def play_options(self, i: int) -> list[dict[str, Any]]:
        hand = self.seats[i].cards
        prev = self.lead if self.lead_by is not None and self.lead_by != i else None
        pats = candidates(hand, self.game, prev)
        pats = self._filter(i, pats, prev)
        opts = [{"kind": "play", "cards": list(p.cards), "label": p.label()} for p in pats]
        if prev is not None and self._may_pass(i, opts):
            opts.insert(0, {"kind": "pass", "label": "不要"})
        return opts

    def _filter(self, i: int, pats: list[Pattern], prev: Pattern | None) -> list[Pattern]:
        return pats

    def _may_pass(self, i: int, opts: list[dict[str, Any]]) -> bool:
        return True

    def legal(self, i: int) -> list[dict[str, Any]]:
        if self.turn != i or self.phase not in ("bid", "play"):
            return []
        if self.phase == "bid":
            return self.bid_options(i)
        return self.play_options(i)

    def bid_options(self, i: int) -> list[dict[str, Any]]:
        return []

    def act(self, i: int, option: dict[str, Any]) -> dict[str, Any]:
        if self.turn != i:
            raise ShedError("还没轮到")
        kind = option.get("kind")
        if self.phase == "bid":
            if kind != "bid":
                raise ShedError("现在是叫地主")
            return self._bid(i, int(option.get("bid", 0)))
        if self.phase != "play":
            raise ShedError("这一局还没开始")
        if kind == "pass":
            if not any(o["kind"] == "pass" for o in self.play_options(i)):
                raise ShedError("现在不能不要" + ("（有大必压）" if self.game == "pdk" else ""))
            return self._pass(i)
        if kind == "play":
            return self._play(i, list(option.get("cards") or []))
        raise ShedError("不认识的动作")

    def _pass(self, i: int) -> dict[str, Any]:
        s = self.seats[i]
        s.last = "不要"
        self.passes += 1
        entry = {"seat": s.id, "name": s.name, "kind": "pass"}
        self.history.append(entry)
        self.turn = self._nxt(i)
        if self.passes >= len(self.seats) - 1:  # 别人都不要：上一手的人重新出
            self.turn = self.lead_by
            self.lead, self.lead_by, self.passes = None, None, 0
            for o in self.seats:
                o.last = ""
        return entry

    def _play(self, i: int, cards: list[str]) -> dict[str, Any]:
        s = self.seats[i]
        hand = list(s.cards)
        for c in cards:
            if c not in hand:
                raise ShedError("手上没有这张牌")
            hand.remove(c)
        pat = classify(cards, self.game, last=not hand)
        if pat is None:
            raise ShedError("这几张凑不成牌型")
        legal = self.play_options(i)
        if not any(o["kind"] == "play" and sorted(o["cards"]) == sorted(cards) for o in legal):
            prev = self.lead if self.lead_by not in (None, i) else None
            if prev is not None and not beats(pat, prev):
                raise ShedError("压不过上一手")
            if not self._allow_custom(i, pat):
                raise ShedError("按规则这手不能这样出")
        s.cards = sort_cards(hand)
        s.plays += 1
        s.last = pat.label()
        self.lead, self.lead_by, self.passes = pat, i, 0
        entry = {
            "seat": s.id,
            "name": s.name,
            "kind": "play",
            "cards": list(pat.cards),
            "label": pat.label(),
        }
        self.history.append(entry)
        self._on_play(i, pat)
        if not s.cards:
            self._finish(i)
        else:
            self.turn = self._nxt(i)
        return entry

    def _allow_custom(self, i: int, pat: Pattern) -> bool:
        """你自己选的牌不在常用组合里（比如带了别的牌）：只要牌型合法、压得过就行。"""
        return True

    def _on_play(self, i: int, pat: Pattern) -> None:
        return None

    def _finish(self, winner: int) -> None:
        raise NotImplementedError

    def winners(self) -> list[str]:
        return [r["seat"] for r in self.results if r["points"] > 0]

    # --- 记牌器和出法标注（给成员看，人人一样） -----------------------------------------

    def full_deck(self) -> list[str]:
        return ddz_deck(random.Random(0)) if self.game == "ddz" else pdk_deck(random.Random(0))

    def played_cards(self) -> list[str]:
        return [c for h in self.history if h.get("kind") == "play" for c in h["cards"]]

    def outside(self, i: int) -> Counter:
        """在别人手里（还没出）的牌，按点数数张数。"""
        left = Counter(value(c) for c in self.full_deck())
        left.subtract(value(c) for c in self.seats[i].cards)
        left.subtract(value(c) for c in self.played_cards())
        if self.game == "ddz" and getattr(self, "landlord", None) is None:
            left.subtract(value(c) for c in getattr(self, "bottom", []))  # 底牌还没人拿
        return +left

    def teammate(self, i: int, j: int | None) -> bool:
        return False

    def tracker(self, i: int) -> str:
        out = self.outside(i)
        big = " ".join(f"{name_of(v)}×{out[v]}" for v in sorted(out, reverse=True) if out[v])
        bombs = [name_of(v) for v, n in out.items() if n == 4]
        lines = [f"别人手里还有（从大到小）：{big or '（没了）'}"]
        if self.game == "ddz" and out.get(16) and out.get(17):
            bombs.append("王炸")
        lines.append("别人可能还有的炸弹：" + ("、".join(bombs) if bombs else "没有了"))
        lines.append(f"已经出过 {len(self.played_cards())} 张")
        return "\n".join(lines)

    def note_for(self, i: int, o: dict[str, Any]) -> str:
        me = self.seats[i]
        notes: list[str] = []
        lead_by = self.lead_by if self.lead is not None and self.lead_by != i else None
        if o["kind"] == "pass":
            if lead_by is not None and self.teammate(i, lead_by):
                notes.append("让队友的牌走")
            elif lead_by is not None and len(self.seats[lead_by].cards) <= 2:
                notes.append(f"对手只剩 {len(self.seats[lead_by].cards)} 张，不要可能让她走")
            return "，".join(notes)
        if o["kind"] != "play":
            return ""
        cards = o["cards"]
        pat = classify(cards, self.game, last=len(cards) == len(me.cards))
        if len(cards) == len(me.cards):
            return "能一手出完"
        mine = Counter(value(c) for c in me.cards)
        used = Counter(value(c) for c in cards)
        if pat is not None and pat.kind in ("bomb", "rocket"):
            notes.append("用炸弹")
        elif any(mine[v] == 4 and n < 4 for v, n in used.items()):
            notes.append("会拆炸弹")
        if lead_by is not None and self.teammate(i, lead_by):
            notes.append("压了队友")
        if pat is not None and pat.kind in ("single", "pair", "triple"):
            out = self.outside(i)
            higher = any(n >= pat.size and v > pat.main for v, n in out.items())
            bomb = any(n == 4 for n in out.values()) or (out.get(16) and out.get(17))
            if not higher:
                notes.append("外面没人管得上" + ("（除非炸）" if bomb else ""))
        notes.append(f"出完还剩 {len(me.cards) - len(cards)} 张")
        return "，".join(notes)

    # --- 给人看 / 给模型看 -------------------------------------------------------

    def view(self, viewer: str | None, *, reveal_all: bool = False) -> dict[str, Any]:
        seats = []
        for i, s in enumerate(self.seats):
            show = reveal_all or s.id == viewer or self.done
            seats.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "count": len(s.cards),
                    "cards": list(s.cards) if show else [],
                    "role": s.role,
                    "last": s.last,
                    "turn": i == self.turn,
                    "score": s.score,
                    "lead": i == self.lead_by,
                }
            )
        you = next((i for i, s in enumerate(self.seats) if s.id == viewer), None)
        return {
            "round_no": self.round_no,
            "phase": self.phase,
            "seats": seats,
            "lead": {
                "cards": list(self.lead.cards),
                "label": self.lead.label(),
                "by": self.seats[self.lead_by].id,
            }
            if self.lead is not None and self.lead_by is not None
            else None,
            "options": self.legal(you) if you is not None else [],
            "results": list(self.results),
            "history": list(self.history[-24:]),
        }

    def brief(self, i: int) -> dict[str, str]:
        s = self.seats[i]
        lines = []
        for j, o in enumerate(self.seats):
            tag = "（你）" if j == i else ""
            role = f"，{o.role}" if o.role else ""
            lines.append(f"{o.name}{tag}{role}：还剩 {len(o.cards)} 张")
        recent = []
        for h in self.history[-12:]:
            if h["kind"] == "play":
                recent.append(f"{h['name']}：{h['label']}（{cards_zh(h['cards'])}）")
            elif h["kind"] == "pass":
                recent.append(f"{h['name']}：不要")
            else:
                recent.append(f"{h['name']}：{h['label']}")
        if self.phase == "bid":
            last = "现在是叫地主"
        elif self.lead is None or self.lead_by in (None, i):
            last = "轮到你先出，出什么都行"
        else:
            who = self.seats[self.lead_by].name
            last = f"要压 {who} 出的 {self.lead.label()}（{cards_zh(list(self.lead.cards))}）"
        return {
            "seat": s.role or "（还没定）",
            "hand": cards_zh(s.cards),
            "table": "\n".join(lines),
            "last": last,
            "history": "\n".join(recent) or "（还没人出牌）",
        }


def option_text(o: dict[str, Any]) -> str:
    if o.get("text"):  # 升级：标签里已经带了牌
        return str(o["text"])
    if o["kind"] == "pass":
        return "不要"
    if o["kind"] == "bid":
        return "不叫" if o["bid"] == 0 else f"叫 {o['bid']} 分"
    return f"{o['label']}：{cards_zh(o['cards'])}"


def trim_options(opts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """选项太多时留前面的（小的在前）和所有炸弹。"""
    if len(opts) <= MAX_OPTIONS:
        return opts
    bombs = [o for o in opts if o.get("label", "").startswith(("炸弹", "王炸"))]
    head = [o for o in opts if o not in bombs][: MAX_OPTIONS - len(bombs)]
    return head + bombs


# --- 斗地主 ------------------------------------------------------------------------


@dataclass
class Doudizhu(Shedding):
    game: str = "ddz"
    bottom: list[str] = field(default_factory=list)
    bid: int = 0
    bidder: int | None = None
    bids_done: int = 0
    landlord: int | None = None
    bombs: int = 0
    redeals: int = 0

    def start(self) -> None:
        if not self.can_start():
            raise ShedError("这一局还没打完")
        self.round_no += 1
        self._deal()
        self.first = self.rng.randrange(3)
        self.turn = self.first

    def _deal(self) -> None:
        deck = ddz_deck(self.rng)
        for k, s in enumerate(self.seats):
            s.cards = sort_cards(deck[k * 17 : (k + 1) * 17])
            s.role, s.plays, s.last = "", 0, ""
        self.bottom = deck[51:]
        self.phase = "bid"
        self.bid, self.bidder, self.bids_done = 0, None, 0
        self.landlord, self.bombs = None, 0
        self.lead, self.lead_by, self.passes = None, None, 0
        self.history, self.results = [], []

    def bid_options(self, i: int) -> list[dict[str, Any]]:
        opts = [{"kind": "bid", "bid": 0, "label": "不叫"}]
        opts += [
            {"kind": "bid", "bid": b, "label": f"叫 {b} 分"} for b in (1, 2, 3) if b > self.bid
        ]
        return opts

    def _bid(self, i: int, b: int) -> dict[str, Any]:
        if b != 0 and b <= self.bid or b not in (0, 1, 2, 3):
            raise ShedError("要比前面叫得高")
        s = self.seats[i]
        label = "不叫" if b == 0 else f"叫 {b} 分"
        s.last = label
        if b:
            self.bid, self.bidder = b, i
        self.bids_done += 1
        entry = {"seat": s.id, "name": s.name, "kind": "bid", "bid": b, "label": label}
        self.history.append(entry)
        if self.bid == 3 or self.bids_done >= 3:
            if self.bidder is None:  # 都不叫：重新发牌（第三次还没人叫，先叫的人当地主，1 分）
                self.redeals += 1
                if self.redeals < 3:
                    self._deal()
                    self.turn = self.first = self._nxt(self.first)
                    return entry
                self.bid, self.bidder = 1, self.first
            self._set_landlord(self.bidder)
        else:
            self.turn = self._nxt(i)
        return entry

    def _set_landlord(self, i: int) -> None:
        self.landlord = i
        for j, s in enumerate(self.seats):
            s.role = "地主" if j == i else "农民"
            s.last = ""
        self.seats[i].cards = sort_cards(self.seats[i].cards + self.bottom)
        self.phase = "play"
        self.turn = i
        self.redeals = 0

    def teammate(self, i: int, j: int | None) -> bool:
        lord = self.landlord
        return j is not None and lord is not None and i != j and lord not in (i, j)

    def _on_play(self, i: int, pat: Pattern) -> None:
        if pat.kind in ("bomb", "rocket"):
            self.bombs += 1

    def _finish(self, winner: int) -> None:
        self.turn = None
        assert self.landlord is not None
        lord = self.landlord
        lord_won = winner == lord
        farmers = [j for j in range(3) if j != lord]
        spring = (lord_won and all(self.seats[j].plays == 0 for j in farmers)) or (
            not lord_won and self.seats[lord].plays == 1
        )
        mult = self.bid * (2**self.bombs) * (2 if spring else 1)
        self.results = []
        for j, s in enumerate(self.seats):
            if j == lord:
                pts = 2 * mult if lord_won else -2 * mult
            else:
                pts = -mult if lord_won else mult
            s.score += pts
            self.results.append(
                {"seat": s.id, "name": s.name, "role": s.role, "points": pts, "left": len(s.cards)}
            )
        self.summary = {
            "winner": "地主" if lord_won else "农民",
            "bid": self.bid,
            "bombs": self.bombs,
            "spring": spring,
            "mult": mult,
        }
        self.phase = "done"

    def view(self, viewer: str | None, *, reveal_all: bool = False) -> dict[str, Any]:
        data = super().view(viewer, reveal_all=reveal_all)
        data.update(
            {
                "game": "doudizhu",
                "bid": self.bid,
                "bombs": self.bombs,
                "bottom": list(self.bottom) if self.landlord is not None else [],
                "summary": getattr(self, "summary", None) if self.done else None,
            }
        )
        return data


# --- 跑得快 ------------------------------------------------------------------------


@dataclass
class Paodekuai(Shedding):
    game: str = "pdk"

    def start(self) -> None:
        if not self.can_start():
            raise ShedError("这一局还没打完")
        self.round_no += 1
        deck = pdk_deck(self.rng)
        for k, s in enumerate(self.seats):
            s.cards = sort_cards(deck[k * 16 : (k + 1) * 16])
            s.role, s.plays, s.last = "", 0, ""
        self.phase = "play"
        self.lead, self.lead_by, self.passes = None, None, 0
        self.history, self.results = [], []
        # 第一局黑桃 3 先出；之后上一局赢的人先出
        holder = next(i for i, s in enumerate(self.seats) if "3s" in s.cards)
        self.first = (
            holder
            if self.round_no == 1 or getattr(self, "last_winner", None) is None
            else self.last_winner
        )
        self.turn = self.first

    def _may_pass(self, i: int, opts: list[dict[str, Any]]) -> bool:
        return not any(o["kind"] == "play" for o in opts)  # 有大必压

    def _filter(self, i: int, pats: list[Pattern], prev: Pattern | None) -> list[Pattern]:
        nxt = self.seats[self._nxt(i)]
        if len(nxt.cards) == 1:  # 下家报单：出单张必须出最大的
            top = max(value(c) for c in self.seats[i].cards)
            pats = [p for p in pats if p.kind != "single" or p.main == top]
        if self.round_no == 1 and not self.history and "3s" in self.seats[i].cards:
            pats = [p for p in pats if "3s" in p.cards]  # 第一手要带黑桃 3
        return pats

    def _allow_custom(self, i: int, pat: Pattern) -> bool:
        nxt = self.seats[self._nxt(i)]
        if pat.kind == "single" and len(nxt.cards) == 1:
            return pat.main == max(value(c) for c in self.seats[i].cards)
        if self.round_no == 1 and not self.history and "3s" in self.seats[i].cards:
            return "3s" in pat.cards
        return True

    def _finish(self, winner: int) -> None:
        self.turn = None
        self.last_winner = winner
        self.results = []
        total = 0
        rows = []
        for j, s in enumerate(self.seats):
            if j == winner:
                continue
            left = len(s.cards)
            pay = (
                0 if left == 1 else left * (2 if s.plays == 0 else 1)
            )  # 剩一张不扣；一张没出（被关）翻倍
            total += pay
            rows.append((j, -pay))
        rows.append((winner, total))
        for j, pts in sorted(rows):
            s = self.seats[j]
            s.score += pts
            self.results.append(
                {
                    "seat": s.id,
                    "name": s.name,
                    "role": "",
                    "points": pts,
                    "left": len(s.cards),
                    "shut": s.plays == 0 and j != winner,
                }
            )
        self.phase = "done"

    def view(self, viewer: str | None, *, reveal_all: bool = False) -> dict[str, Any]:
        data = super().view(viewer, reveal_all=reveal_all)
        data["game"] = "paodekuai"
        return data

    def winners(self) -> list[str]:
        """先出完的人赢（别人都只剩一张时她拿 0 分，也算赢）。"""
        return [r["seat"] for r in self.results if r["left"] == 0]


__all__ = [
    "KIND_ZH",
    "Doudizhu",
    "Hand",
    "Paodekuai",
    "ShedError",
    "Shedding",
    "option_text",
    "trim_options",
]
