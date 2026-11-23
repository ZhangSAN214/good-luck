"""德州扑克（无限注）规则：发牌、盲注、下注轮、边池、比牌。纯逻辑，不调模型、不碰数据库。

座位里谁是你、谁是成员都一样走这套规则；
谁来「想」由 ``hub`` 决定（你点按钮 / 成员的模型 / 本地电脑）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.arcade.cards import cards_zh, new_deck
from roundtable.core.arcade.poker_eval import best_hand, hand_name

STREET_ZH = {"preflop": "翻牌前", "flop": "翻牌", "turn": "转牌", "river": "河牌", "done": "结束"}
KIND_ZH = {"fold": "弃牌", "check": "过牌", "call": "跟注", "raise": "加注", "allin": "全下"}


class HoldemError(ValueError):
    """不合规则的操作（不是你的回合、钱不够、加注太少……）。"""


@dataclass
class Seat:
    id: str  # "you" 或成员 id
    name: str
    stack: int
    hole: list[str] = field(default_factory=list)
    bet: int = 0  # 这一轮下了多少
    put: int = 0  # 这一手一共下了多少
    folded: bool = False
    all_in: bool = False
    acted: bool = False
    out: bool = False  # 没筹码了，这手不参加
    last: str = ""  # 上一个动作（显示在座位上）

    @property
    def is_you(self) -> bool:
        return self.id == "you"


@dataclass
class Holdem:
    seats: list[Seat]
    small_blind: int = 10
    big_blind: int = 20
    rng: random.Random = field(default_factory=random.Random)
    hand_no: int = 0
    dealer: int = -1
    street: str = "idle"  # idle / preflop / flop / turn / river / done
    board: list[str] = field(default_factory=list)
    deck: list[str] = field(default_factory=list)
    turn: int | None = None
    current: int = 0  # 这一轮最高下注
    min_raise: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)  # 这一手的动作
    results: list[dict[str, Any]] = field(default_factory=list)  # 这一手的输赢（结束后才有）

    # --- 查询 -------------------------------------------------------------------

    def index_of(self, seat_id: str) -> int:
        for i, s in enumerate(self.seats):
            if s.id == seat_id:
                return i
        raise HoldemError("没有这个座位")

    @property
    def pot(self) -> int:
        return sum(s.put for s in self.seats)

    def alive(self) -> list[int]:
        """这一手还没弃牌的人。"""
        return [i for i, s in enumerate(self.seats) if not s.out and not s.folded]

    def can_act(self, i: int) -> bool:
        s = self.seats[i]
        return not s.out and not s.folded and not s.all_in

    def players_with_chips(self) -> list[int]:
        return [i for i, s in enumerate(self.seats) if s.stack > 0]

    def _next(self, i: int, ok) -> int | None:
        n = len(self.seats)
        for k in range(1, n + 1):
            j = (i + k) % n
            if ok(j):
                return j
        return None

    def to_call(self, i: int) -> int:
        s = self.seats[i]
        return min(self.current - s.bet, s.stack)

    def raise_bounds(self, i: int) -> tuple[int, int] | None:
        """能加到多少：(最少加到, 最多加到=全下)；不能加时 None。"""
        s = self.seats[i]
        max_to = s.bet + s.stack
        if max_to <= self.current:
            return None
        min_to = self.current + (self.min_raise or self.big_blind)
        return (min(min_to, max_to), max_to)

    def legal(self, i: int) -> list[dict[str, Any]]:
        """轮到第 i 位时可以做的事。

        加注给几个常用档（最少、半个底池、一个底池、全下），你自己可以填任意数。
        """
        if self.turn != i:
            return []
        s = self.seats[i]
        need = self.current - s.bet
        opts: list[dict[str, Any]] = []
        if need > 0:
            opts.append({"kind": "fold"})
            if s.stack <= need:
                opts.append({"kind": "allin", "to": s.bet + s.stack})
                return opts
            opts.append({"kind": "call", "to": self.current})
        else:
            opts.append({"kind": "check"})
        bounds = self.raise_bounds(i)
        if bounds:
            lo, hi = bounds
            pot_after_call = self.pot + need
            targets = {lo, self.current + pot_after_call // 2, self.current + pot_after_call}
            for to in sorted(t for t in targets if lo <= t < hi):
                opts.append({"kind": "raise", "to": to})
            opts.append({"kind": "allin", "to": hi})
        return opts

    # --- 一手牌 -----------------------------------------------------------------

    def can_start(self) -> bool:
        return len(self.players_with_chips()) >= 2

    def start_hand(self) -> None:
        if self.street not in ("idle", "done"):
            raise HoldemError("这一手还没打完")
        if not self.can_start():
            raise HoldemError("有筹码的人不到两位，开不了局")
        self.hand_no += 1
        for s in self.seats:
            s.hole, s.bet, s.put = [], 0, 0
            s.folded = s.all_in = s.acted = False
            s.out = s.stack <= 0
            s.last = ""
        self.board, self.history, self.results = [], [], []
        self.deck = new_deck(self.rng)
        active = lambda j: not self.seats[j].out  # noqa: E731
        self.dealer = self._next(self.dealer, active)  # type: ignore[assignment]
        for _ in range(2):
            for j, s in enumerate(self.seats):
                if active(j):
                    s.hole.append(self.deck.pop())
        heads_up = len([j for j in range(len(self.seats)) if active(j)]) == 2
        sb = self.dealer if heads_up else self._next(self.dealer, active)
        bb = self._next(sb, active)  # type: ignore[arg-type]
        assert sb is not None and bb is not None
        self._pay(sb, self.small_blind)
        self._pay(bb, self.big_blind)
        self.seats[sb].last = f"小盲 {self.seats[sb].bet}"
        self.seats[bb].last = f"大盲 {self.seats[bb].bet}"
        self.current = max(self.seats[sb].bet, self.seats[bb].bet)
        self.min_raise = self.big_blind
        self.street = "preflop"
        self.turn = self._next(bb, self.can_act)
        self._settle_if_stuck()

    def _pay(self, i: int, amount: int) -> int:
        s = self.seats[i]
        pay = max(0, min(amount, s.stack))
        s.stack -= pay
        s.bet += pay
        s.put += pay
        if s.stack == 0:
            s.all_in = True
        return pay

    def act(self, i: int, kind: str, to: int | None = None) -> dict[str, Any]:
        """第 i 位做一个动作；返回记下来的那一条。"""
        if self.street in ("idle", "done") or self.turn != i:
            raise HoldemError("还没轮到")
        s = self.seats[i]
        need = self.current - s.bet
        if kind == "fold":
            s.folded = True
        elif kind == "check":
            if need > 0:
                raise HoldemError("前面有人下注，不能过牌")
        elif kind == "call":
            if need <= 0:
                raise HoldemError("没有要跟的注，可以过牌")
            self._pay(i, need)
        elif kind in ("raise", "allin"):
            bounds = self.raise_bounds(i)
            hi = s.bet + s.stack
            if kind == "allin":
                to = hi
            if to is None:
                raise HoldemError("加注要写加到多少")
            to = int(to)
            if bounds is None:
                if kind == "allin" and need > 0:  # 钱不够跟注：全下就是跟到底
                    self._pay(i, s.stack)
                    kind = "allin"
                else:
                    raise HoldemError("不能再加注了")
            else:
                lo, _ = bounds
                if to > hi:
                    raise HoldemError(f"最多只能加到 {hi}")
                if to < lo and to != hi:
                    raise HoldemError(f"最少要加到 {lo}")
                raised = to - self.current
                self._pay(i, to - s.bet)
                if raised >= self.min_raise:
                    self.min_raise = raised
                    for j, other in enumerate(self.seats):  # 加满了：别人要重新表态
                        if j != i:
                            other.acted = False
                if to > self.current:
                    self.current = to
                if s.stack == 0:
                    kind = "allin"
        else:
            raise HoldemError("不认识的动作")
        s.acted = True
        s.last = KIND_ZH[kind] + (f" {s.bet}" if kind in ("call", "raise", "allin") else "")
        entry = {"seat": s.id, "name": s.name, "street": self.street, "kind": kind, "bet": s.bet}
        self.history.append(entry)
        self._advance(i)
        return entry

    # --- 推进 -------------------------------------------------------------------

    def _round_done(self) -> bool:
        for j, s in enumerate(self.seats):
            if self.can_act(j) and (not s.acted or s.bet < self.current):
                return False
        return True

    def _advance(self, last: int) -> None:
        if len(self.alive()) == 1:
            self._finish()
            return
        if not self._round_done():
            self.turn = self._next(
                last,
                lambda j: (
                    self.can_act(j)
                    and (not self.seats[j].acted or self.seats[j].bet < self.current)
                ),
            )
            return
        self._next_street()

    def _settle_if_stuck(self) -> None:
        """开局时（盲注就全下了）或这一轮没人能动：直接往下发。"""
        if self.turn is None or self._round_done():
            self._next_street()

    def _next_street(self) -> None:
        while True:
            for s in self.seats:
                s.bet, s.acted = 0, False
            self.current, self.min_raise = 0, self.big_blind
            if self.street == "river":
                self._finish()
                return
            self.street = {"preflop": "flop", "flop": "turn", "turn": "river"}[self.street]
            self.board += [self.deck.pop() for _ in range(3 if self.street == "flop" else 1)]
            movers = [j for j in range(len(self.seats)) if self.can_act(j)]
            if len(movers) >= 2:
                self.turn = self._next(self.dealer, self.can_act)
                return
            # 只剩一位（或没人）还能下注：不用再下注，把牌发完比大小

    def _finish(self) -> None:
        self.turn = None
        alive = self.alive()
        won = {i: 0 for i in range(len(self.seats))}
        scores: dict[int, tuple[tuple[int, ...], tuple[str, ...]]] = {}
        if len(alive) == 1:
            won[alive[0]] = self.pot
        else:
            for i in alive:
                scores[i] = best_hand(self.seats[i].hole + self.board)
            levels = sorted({s.put for s in self.seats if s.put > 0})
            prev = 0
            for level in levels:
                layer = sum(max(0, min(s.put, level) - prev) for s in self.seats)
                eligible = [i for i in alive if self.seats[i].put >= level]
                if not eligible:  # 弃牌的人多下的：退回（不会发生在正常流程里）
                    prev = level
                    continue
                top = max(scores[i][0] for i in eligible)
                winners = [i for i in eligible if scores[i][0] == top]
                share, rest = divmod(layer, len(winners))
                for i in winners:
                    won[i] += share
                if rest:  # 零头给庄家左手边第一位赢家
                    first = self._next(self.dealer, lambda j, w=winners: j in w)
                    won[first if first is not None else winners[0]] += rest
                prev = level
        self.results = []
        for i, s in enumerate(self.seats):
            if s.out:
                continue
            s.stack += won[i]
            row: dict[str, Any] = {
                "seat": s.id,
                "name": s.name,
                "won": won[i],
                "net": won[i] - s.put,
                "folded": s.folded,
            }
            if i in scores:
                row["hand"] = hand_name(scores[i][0])
                row["best"] = list(scores[i][1])
            self.results.append(row)
        self.street = "done"

    # --- 给人看 / 给模型看 -------------------------------------------------------

    def winners(self) -> list[str]:
        return [r["seat"] for r in self.results if r["won"] > 0]

    def view(self, *, reveal_all: bool = False) -> dict[str, Any]:
        """网页上显示的牌桌。别人的底牌只在比牌时（没弃牌的）或 reveal_all（旁观）时给。"""
        showdown = self.street == "done" and len(self.alive()) > 1
        seats = []
        for i, s in enumerate(self.seats):
            show = s.is_you or reveal_all or (showdown and not s.folded)
            seats.append(
                {
                    "id": s.id,
                    "name": s.name,
                    "stack": s.stack,
                    "bet": s.bet,
                    "put": s.put,
                    "folded": s.folded,
                    "all_in": s.all_in,
                    "out": s.out,
                    "last": s.last,
                    "dealer": i == self.dealer,
                    "turn": i == self.turn,
                    "hole": list(s.hole) if show else (["??", "??"] if s.hole else []),
                }
            )
        you = next((i for i, s in enumerate(self.seats) if s.is_you), None)
        return {
            "hand_no": self.hand_no,
            "street": self.street,
            "street_zh": STREET_ZH.get(self.street, ""),
            "board": list(self.board),
            "pot": self.pot,
            "current": self.current,
            "blinds": [self.small_blind, self.big_blind],
            "seats": seats,
            "options": self.legal(you) if you is not None else [],
            "to_call": self.to_call(you) if you is not None and self.turn == you else 0,
            "raise": self.raise_bounds(you) if you is not None and self.turn == you else None,
            "results": list(self.results),
            "history": list(self.history[-30:]),
        }

    def brief(self, i: int) -> dict[str, str]:
        """给出牌的成员看的局面（中文，只含她自己能看到的牌）。"""
        s = self.seats[i]
        lines = []
        for j, o in enumerate(self.seats):
            if o.out:
                continue
            tag = "（你）" if j == i else ""
            tag += "（庄）" if j == self.dealer else ""
            state = "已弃牌" if o.folded else ("已全下" if o.all_in else f"本轮已下 {o.bet}")
            lines.append(f"{o.name}{tag}：剩 {o.stack}，{state}")
        acts = [
            f"{STREET_ZH[h['street']]}·{h['name']}：{KIND_ZH[h['kind']]}"
            + (f" 到 {h['bet']}" if h["kind"] in ("call", "raise", "allin") else "")
            for h in self.history
        ]
        return {
            "street": STREET_ZH[self.street],
            "hole": cards_zh(s.hole),
            "board": cards_zh(self.board),
            "pot": str(self.pot),
            "to_call": str(self.to_call(i)),
            "stack": str(s.stack),
            "table": "\n".join(lines),
            "actions": "\n".join(acts) or "（这一手还没人动）",
        }
