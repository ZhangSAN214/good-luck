"""麻将（大众推倒胡）规则：四人、136 张（万、条、筒、东南西北中发白，不带花）。
纯逻辑，不碰网页和模型。

- 能碰、能杠（明杠、暗杠、补杠），不吃；点炮、自摸都能胡。
  一炮多响时按出牌后的座次，离点炮的人最近的胡（截胡）。
- 胡牌：四组（顺子 / 刻子）加一对，或者七对。
- 番（加起来）：平胡 1 / 七对 2 / 碰碰胡 2；清一色 +2、混一色 +1；
  自摸 +1、杠上开花 +1、海底捞月 +1。
- 结算：自摸三家各付番数；点炮的一家付两倍番数。杠分在这一局结束时结算（流局也算）：暗杠三家各付 2，
  明杠点杠的人付 3，补杠三家各付 1。
- 庄家胡了或者流局，庄家连庄；别人胡了，庄家换下一位。

和斗地主 / 跑得快共用 ``Shedding`` 的外壳（座位、轮到谁、结果、给网页和模型看的接口），
所以总管不用另写一套。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from roundtable.core.arcade.shedding import Shedding, ShedError

SUITS = ("m", "s", "p")  # 万、条、筒
SUIT_ZH = {"m": "万", "s": "条", "p": "筒"}
NUM_ZH = "一二三四五六七八九"
HONORS_ZH = "东南西北中发白"


def all_tiles() -> list[str]:
    return [f"{n}{s}" for s in SUITS for n in range(1, 10)] + [f"{n}z" for n in range(1, 8)]


TILES = all_tiles()
INDEX = {t: k for k, t in enumerate(TILES)}


def tile_zh(t: str) -> str:
    n, s = int(t[0]), t[1]
    return HONORS_ZH[n - 1] if s == "z" else f"{NUM_ZH[n - 1]}{SUIT_ZH[s]}"


def tiles_zh(ts: list[str]) -> str:
    return " ".join(tile_zh(t) for t in sort_tiles(ts)) or "（没有）"


def sort_tiles(ts: list[str]) -> list[str]:
    return sorted(ts, key=lambda t: INDEX[t])


def counts_of(ts: list[str]) -> list[int]:
    c = [0] * 34
    for t in ts:
        c[INDEX[t]] += 1
    return c


# --- 胡牌判断和向听数 -------------------------------------------------------------


def _complete(c: list[int], need_pair: bool) -> bool:
    """c 能不能全部拆成顺子 / 刻子（need_pair 时还要恰好一对）。会临时改 c，返回前复原。"""
    i = next((k for k in range(34) if c[k]), -1)
    if i < 0:
        return not need_pair
    if need_pair and c[i] >= 2:
        c[i] -= 2
        ok = _complete(c, False)
        c[i] += 2
        if ok:
            return True
    if c[i] >= 3:
        c[i] -= 3
        ok = _complete(c, need_pair)
        c[i] += 3
        if ok:
            return True
    if i < 27 and i % 9 <= 6 and c[i + 1] and c[i + 2]:
        c[i] -= 1
        c[i + 1] -= 1
        c[i + 2] -= 1
        ok = _complete(c, need_pair)
        c[i] += 1
        c[i + 1] += 1
        c[i + 2] += 1
        if ok:
            return True
    return False


def seven_pairs(c: list[int]) -> bool:
    return sum(c) == 14 and all(n in (0, 2, 4) for n in c)


def is_win(hand: list[str]) -> bool:
    """手上的牌（不算碰杠下来的）能不能胡。"""
    if len(hand) % 3 != 2:
        return False
    c = counts_of(hand)
    return seven_pairs(c) or _complete(c, True)


def shanten(hand: list[str], melds: int = 0) -> int:
    """离听牌还差几步（0 = 听牌，-1 = 已经胡了）。手牌 3n+1 或 3n+2 张都行。

    每一门（万 / 条 / 筒 / 字）分开拆，各自缓存能拆出的（组, 搭子, 对）组合，再合起来挑最好的。
    """
    c = counts_of(hand)
    need = 4 - melds
    parts = [_suit_parts(tuple(c[k * 9 : k * 9 + 9]), False) for k in range(3)]
    parts.append(_suit_parts(tuple(c[27:34]), True))
    best = 8
    for m1, t1, p1 in parts[0]:
        for m2, t2, p2 in parts[1]:
            for m3, t3, p3 in parts[2]:
                for m4, t4, p4 in parts[3]:
                    p = p1 + p2 + p3 + p4
                    if p > 1:
                        continue
                    m = min(m1 + m2 + m3 + m4, need)
                    t = min(t1 + t2 + t3 + t4, need - m)
                    best = min(best, 8 - 2 * (m + melds) - t - p)
    if melds == 0:
        pairs = sum(1 for n in c if n >= 2)
        kinds = sum(1 for n in c if n)
        best = min(best, 6 - pairs + max(0, 7 - kinds))
    return best


@lru_cache(maxsize=1 << 16)
def _suit_parts(key: tuple[int, ...], honor: bool) -> tuple[tuple[int, int, int], ...]:
    """一门牌能拆成的（组, 搭子, 对）组合（只留不被别的组合全面压过的）。"""
    c = list(key)
    n = len(c)
    found: set[tuple[int, int, int]] = set()

    def dfs(i: int, m: int, t: int, p: int) -> None:
        while i < n and not c[i]:
            i += 1
        if i >= n:
            found.add((m, t, p))
            return
        if c[i] >= 3:
            c[i] -= 3
            dfs(i, m + 1, t, p)
            c[i] += 3
        if not honor and i <= n - 3 and c[i + 1] and c[i + 2]:
            c[i] -= 1
            c[i + 1] -= 1
            c[i + 2] -= 1
            dfs(i, m + 1, t, p)
            c[i] += 1
            c[i + 1] += 1
            c[i + 2] += 1
        if c[i] >= 2:
            c[i] -= 2
            if not p:
                dfs(i, m, t, 1)
            dfs(i, m, t + 1, p)
            c[i] += 2
        if not honor and i <= n - 2 and c[i + 1]:
            c[i] -= 1
            c[i + 1] -= 1
            dfs(i, m, t + 1, p)
            c[i] += 1
            c[i + 1] += 1
        if not honor and i <= n - 3 and c[i + 2]:
            c[i] -= 1
            c[i + 2] -= 1
            dfs(i, m, t + 1, p)
            c[i] += 1
            c[i + 2] += 1
        c[i] -= 1
        dfs(i + 1, m, t, p)
        c[i] += 1

    dfs(0, 0, 0, 0)
    keep = [
        x
        for x in found
        if not any(
            y != x and y[0] >= x[0] and y[0] + y[1] >= x[0] + x[1] and y[2] == x[2] and y[1] >= x[1]
            for y in found
        )
    ]
    return tuple(sorted(keep))


def waits(hand: list[str], seen: Counter | None = None) -> list[str]:
    """听牌时等哪些牌（手牌 3n+1 张）。seen：已经看得到的张数（四张都看见了就不算）。"""
    mine = Counter(hand)
    out = []
    for t in TILES:
        if mine[t] + (seen[t] if seen else 0) >= 4:
            continue
        if is_win([*hand, t]):
            out.append(t)
    return out


# --- 牌局 -------------------------------------------------------------------------


@dataclass
class Mahjong(Shedding):
    """四人推倒胡。``seats[i].cards`` 是手牌（不含碰杠下来的）。"""

    game: str = "mj"
    wall: list[str] = field(default_factory=list)
    dealer: int = 0
    melds: list[list[dict[str, Any]]] = field(default_factory=list)
    rivers: list[list[str]] = field(default_factory=list)
    drawn: str | None = None  # 这一轮刚摸的牌
    last_discard: tuple[int, str] | None = None
    claims: list[tuple[int, str]] = field(default_factory=list)  # 排队问的人：(座位, hu / pong)
    kong_draw: bool = False  # 刚杠完补的牌（杠上开花）
    claimed: bool = False  # 刚碰完：只能打牌
    kongs: list[dict[str, Any]] = field(default_factory=list)  # 这一局的杠：{seat, kind, from}
    summary: dict[str, Any] | None = None
    started: bool = False

    # --- 开局 --------------------------------------------------------------------

    def can_start(self) -> bool:
        return self.phase in ("idle", "done")

    def start(self) -> None:
        if self.started:
            w = self.summary.get("winner_seat") if self.summary else None
            if w is not None and w != self.dealer:
                self.dealer = (self.dealer + 1) % 4
        else:
            self.dealer = self.rng.randrange(4)
            self.started = True
        self.round_no += 1
        wall = [t for t in TILES for _ in range(4)]
        self.rng.shuffle(wall)
        self.wall = wall
        self.melds = [[] for _ in self.seats]
        self.rivers = [[] for _ in self.seats]
        for s in self.seats:
            s.cards = sort_tiles([self.wall.pop() for _ in range(13)])
            s.last, s.role, s.plays = "", "", 0
        self.seats[self.dealer].role = "庄家"
        self.history, self.results, self.kongs = [], [], []
        self.summary, self.last_discard, self.claims = None, None, []
        self.kong_draw = self.claimed = False
        self.phase = "play"
        self.turn = self.dealer
        self._draw(self.dealer)

    def _draw(self, i: int, *, after_kong: bool = False) -> None:
        if not self.wall:
            self._finish_round(None)
            return
        t = self.wall.pop(0 if after_kong else -1)
        self.seats[i].cards.append(t)
        self.drawn = t
        self.kong_draw = after_kong
        self.claimed = False
        self.turn = i
        self.phase = "play"

    # --- 能做什么 -----------------------------------------------------------------

    def legal(self, i: int) -> list[dict[str, Any]]:
        if self.turn != i or self.phase not in ("play", "claim"):
            return []
        if self.phase == "claim":
            return self._claim_options(i)
        return self.play_options(i)

    def play_options(self, i: int) -> list[dict[str, Any]]:
        hand = self.seats[i].cards
        opts: list[dict[str, Any]] = []
        if not self.claimed:
            if is_win(hand):
                opts.append({"kind": "hu", "cards": [], "label": "自摸", "text": "自摸，胡了"})
            if self.wall:
                for t, n in sorted(Counter(hand).items(), key=lambda x: INDEX[x[0]]):
                    if n == 4:
                        opts.append(
                            {
                                "kind": "kong",
                                "cards": [t] * 4,
                                "label": "暗杠",
                                "text": f"暗杠 {tile_zh(t)}",
                            }
                        )
                for m in self.melds[i]:
                    if m["kind"] == "pong" and m["tiles"][0] in hand:
                        t = m["tiles"][0]
                        opts.append(
                            {
                                "kind": "kong",
                                "cards": [t],
                                "label": "补杠",
                                "text": f"补杠 {tile_zh(t)}",
                            }
                        )
        for t in sort_tiles(list(dict.fromkeys(hand))):
            mark = "（刚摸的）" if t == self.drawn and not self.claimed else ""
            opts.append(
                {"kind": "play", "cards": [t], "label": "打", "text": f"打 {tile_zh(t)}{mark}"}
            )
        return opts

    def _claim_options(self, i: int) -> list[dict[str, Any]]:
        if not self.claims or self.claims[0][0] != i or self.last_discard is None:
            return []
        who, t = self.last_discard
        stage = self.claims[0][1]
        name = self.seats[who].name
        if stage == "hu":
            return [
                {"kind": "hu", "cards": [t], "label": "胡", "text": f"胡 {name} 打的 {tile_zh(t)}"},
                {"kind": "pass", "cards": [], "label": "过", "text": "过（不胡）"},
            ]
        n = self.seats[i].cards.count(t)
        opts = []
        if n >= 2:
            opts.append(
                {"kind": "pong", "cards": [t] * 2, "label": "碰", "text": f"碰 {tile_zh(t)}"}
            )
        if n >= 3 and self.wall:
            opts.append(
                {"kind": "kong", "cards": [t] * 3, "label": "杠", "text": f"杠 {tile_zh(t)}"}
            )
        opts.append({"kind": "pass", "cards": [], "label": "过", "text": "过"})
        return opts

    # --- 动作 --------------------------------------------------------------------

    def act(self, i: int, option: dict[str, Any]) -> dict[str, Any]:
        if self.turn != i:
            raise ShedError("还没轮到")
        if self.phase not in ("play", "claim"):
            raise ShedError("这一局还没开始")
        kind = option.get("kind")
        cards = sort_tiles(list(option.get("cards") or []))
        match = next(
            (o for o in self.legal(i) if o["kind"] == kind and sort_tiles(o["cards"]) == cards),
            None,
        )
        if match is None:
            if kind == "play" and len(cards) != 1:
                raise ShedError("一次打一张牌")
            raise ShedError("现在不能这样做")
        s = self.seats[i]
        entry = {
            "seat": s.id,
            "name": s.name,
            "kind": kind,
            "label": match["label"],
            "cards": cards,
        }
        self.history.append(entry)
        if kind == "hu":
            s.last = match["label"]
            if self.phase == "claim":
                self._finish_round(i, from_seat=self.last_discard[0] if self.last_discard else None)
            else:
                self._finish_round(i)
        elif kind == "play":
            self._discard(i, cards[0])
        elif kind == "pass":
            s.last = "过"
            self.claims.pop(0)
            self._next_claim()
        elif kind == "pong":
            self._pong(i)
        elif kind == "kong":
            self._kong(i, match)
        return entry

    def _discard(self, i: int, t: str) -> None:
        s = self.seats[i]
        s.cards.remove(t)
        s.cards = sort_tiles(s.cards)
        s.plays += 1
        s.last = f"打 {tile_zh(t)}"
        self.rivers[i].append(t)
        self.last_discard = (i, t)
        self.drawn = None
        self.claims = []
        for k in range(1, 4):
            j = (i + k) % 4
            if is_win([*self.seats[j].cards, t]):
                self.claims.append((j, "hu"))
        for k in range(1, 4):
            j = (i + k) % 4
            if self.seats[j].cards.count(t) >= 2:
                self.claims.append((j, "pong"))
        self._next_claim()

    def _next_claim(self) -> None:
        if self.claims:
            self.phase = "claim"
            self.turn = self.claims[0][0]
            return
        if self.last_discard is None:
            return
        self.phase = "play"
        self._draw((self.last_discard[0] + 1) % 4)

    def _take_discard(self) -> tuple[int, str]:
        assert self.last_discard is not None
        who, t = self.last_discard
        if self.rivers[who] and self.rivers[who][-1] == t:
            self.rivers[who].pop()
        self.claims = []
        return who, t

    def _pong(self, i: int) -> None:
        who, t = self._take_discard()
        s = self.seats[i]
        s.cards.remove(t)
        s.cards.remove(t)
        self.melds[i].append({"kind": "pong", "tiles": [t] * 3, "from": who})
        s.last = f"碰 {tile_zh(t)}"
        self.turn, self.phase = i, "play"
        self.drawn, self.claimed = None, True

    def _kong(self, i: int, o: dict[str, Any]) -> None:
        s = self.seats[i]
        t = o["cards"][0]
        if self.phase == "claim":  # 明杠别人打的
            who, _ = self._take_discard()
            for _ in range(3):
                s.cards.remove(t)
            self.melds[i].append({"kind": "kong", "tiles": [t] * 4, "from": who})
            self.kongs.append({"seat": i, "kind": "kong", "from": who})
        elif o["label"] == "暗杠":
            for _ in range(4):
                s.cards.remove(t)
            self.melds[i].append({"kind": "ankong", "tiles": [t] * 4, "from": i})
            self.kongs.append({"seat": i, "kind": "ankong", "from": i})
        else:  # 补杠
            s.cards.remove(t)
            m = next(m for m in self.melds[i] if m["kind"] == "pong" and m["tiles"][0] == t)
            m["kind"], m["tiles"] = "kong", [t] * 4
            self.kongs.append({"seat": i, "kind": "addkong", "from": i})
        s.last = f"{o['label']} {tile_zh(t)}"
        s.cards = sort_tiles(s.cards)
        self._draw(i, after_kong=True)

    # --- 结算 --------------------------------------------------------------------

    def fans(self, i: int, *, self_drawn: bool) -> list[tuple[str, int]]:
        hand = self.seats[i].cards
        melds = self.melds[i]
        everything = hand + [t for m in melds for t in m["tiles"]]
        out: list[tuple[str, int]] = []
        c = counts_of(hand)
        if not melds and seven_pairs(c) and not _complete(list(c), True):
            out.append(("七对", 2))
        elif self._all_triplets(hand) and all(m["kind"] != "chow" for m in melds):
            out.append(("碰碰胡", 2))
        else:
            out.append(("平胡", 1))
        suits = {t[1] for t in everything}
        if len(suits) == 1 and "z" not in suits:
            out.append(("清一色", 2))
        elif len(suits) == 2 and "z" in suits:
            out.append(("混一色", 1))
        if self_drawn:
            out.append(("自摸", 1))
            if self.kong_draw:
                out.append(("杠上开花", 1))
            if not self.wall:
                out.append(("海底捞月", 1))
        return out

    @staticmethod
    def _all_triplets(hand: list[str]) -> bool:
        c = Counter(hand)
        pairs = [t for t, n in c.items() if n == 2]
        return len(pairs) == 1 and all(n in (2, 3) for n in c.values())

    def _finish_round(self, winner: int | None, *, from_seat: int | None = None) -> None:
        pts = [0] * 4
        summary: dict[str, Any] = {"winner_seat": winner}
        if winner is not None:
            self_drawn = from_seat is None
            if not self_drawn:
                assert self.last_discard is not None
                self.seats[winner].cards.append(self.last_discard[1])
                self._take_discard()
            fans = self.fans(winner, self_drawn=self_drawn)
            fan = sum(n for _, n in fans)
            if self_drawn:
                for j in range(4):
                    if j != winner:
                        pts[j] -= fan
                        pts[winner] += fan
            else:
                assert from_seat is not None
                pts[from_seat] -= 2 * fan
                pts[winner] += 2 * fan
            summary.update(
                {
                    "winner": self.seats[winner].name,
                    "from": self.seats[from_seat].name if from_seat is not None else "",
                    "self_drawn": self_drawn,
                    "fan": fan,
                    "fans": [f"{name} {n} 番" for name, n in fans],
                    "tile": self.seats[winner].cards[-1] if not self_drawn else self.drawn,
                }
            )
        else:
            summary["winner"] = ""
        for k in self.kongs:
            i = k["seat"]
            if k["kind"] == "kong":
                pts[k["from"]] -= 3
                pts[i] += 3
            else:
                each = 2 if k["kind"] == "ankong" else 1
                for j in range(4):
                    if j != i:
                        pts[j] -= each
                        pts[i] += each
        summary["kong_points"] = len(self.kongs)
        self.summary = summary
        self.results = []
        for j, s in enumerate(self.seats):
            s.score += pts[j]
            tags = []
            if j == self.dealer:
                tags.append("庄家")
            if j == winner:
                tags.append("自摸" if from_seat is None else "胡")
            if from_seat is not None and j == from_seat:
                tags.append("点炮")
            self.results.append(
                {"seat": s.id, "name": s.name, "points": pts[j], "role": " · ".join(tags)}
            )
        self.phase = "done"
        self.turn = None
        self.claims = []

    def winners(self) -> list[str]:
        w = self.summary.get("winner_seat") if self.summary else None
        return [self.seats[w].id] if w is not None else []

    # --- 记牌器和标注（给成员看，人人一样） -----------------------------------------

    def seen(self, i: int) -> Counter:
        """座位 i 看得到的牌（自己的手牌、所有人打出的、碰杠亮出来的；暗杠别人看不到）。"""
        c: Counter = Counter(self.seats[i].cards)
        for r in self.rivers:
            c.update(r)
        for j, ms in enumerate(self.melds):
            for m in ms:
                if m["kind"] != "ankong" or j == i:
                    c.update(m["tiles"])
        return c

    def tracker(self, i: int) -> str:
        seen = self.seen(i)
        gone = [t for t in TILES if seen[t] >= 4 and t not in self.seats[i].cards]
        last_one = [t for t in TILES if seen[t] == 3]
        lines = [f"牌墙还剩 {len(self.wall)} 张"]
        for j, s in enumerate(self.seats):
            if j == i:
                continue
            ms = "、".join(
                ("暗杠" if m["kind"] == "ankong" else "碰" if m["kind"] == "pong" else "杠")
                + ("" if m["kind"] == "ankong" else tile_zh(m["tiles"][0]))
                for m in self.melds[j]
            )
            lines.append(
                f"{s.name}：亮出 {ms or '没有'}；打过 {tiles_zh(self.rivers[j])}"
                + ("（亮了三组以上，小心她快听牌了）" if len(self.melds[j]) >= 3 else "")
            )
        lines.append("四张都见过的牌：" + (tiles_zh(gone) if gone else "没有"))
        lines.append("只剩最后一张没现的牌：" + (tiles_zh(last_one) if last_one else "没有"))
        return "\n".join(lines)

    def note_for(self, i: int, o: dict[str, Any]) -> str:
        hand = self.seats[i].cards
        melds = len(self.melds[i])
        seen = self.seen(i)
        if o["kind"] == "hu":
            fans = self.fans_if_win(i, o)
            return f"胡了：{fans}" if fans else "胡了"
        if o["kind"] == "pass":
            if self.claims and self.claims[0][1] == "hu":
                return "放弃这次胡牌"
            return ""
        if o["kind"] == "pong":
            t = o["cards"][0]
            rest = list(hand)
            rest.remove(t)
            rest.remove(t)
            best = min(shanten([*rest[:k], *rest[k + 1 :]], melds + 1) for k in range(len(rest)))
            return self._shanten_text(best, after="碰了再打一张")
        if o["kind"] == "kong":
            return "杠了多拿杠分，再从牌墙后面补一张"
        t = o["cards"][0]
        rest = list(hand)
        rest.remove(t)
        notes = []
        s = shanten(rest, melds)
        if s == 0:
            w = waits(rest, seen)
            left = sum(max(0, 4 - seen[x]) for x in w)
            notes.append(f"打完听牌，等 {tiles_zh(w)}（外面最多还有 {left} 张）")
        else:
            notes.append(self._shanten_text(s, after="打完"))
        others = [j for j in range(4) if j != i and t in self.rivers[j]]
        if others:
            notes.append("别人打过这张，比较安全")
        elif seen[t] >= 3:
            notes.append("这张外面已经见过三张，比较安全")
        danger = [j for j in range(4) if j != i and len(self.melds[j]) >= 3 and j not in others]
        if danger and t[1] != "z":
            notes.append(f"{self.seats[danger[0]].name}亮了三组，可能在等这种牌")
        return "，".join(notes)

    @staticmethod
    def _shanten_text(s: int, *, after: str) -> str:
        if s < 0:
            return f"{after}就胡了"
        if s == 0:
            return f"{after}听牌"
        return f"{after}离听牌还差 {s} 步"

    def fans_if_win(self, i: int, o: dict[str, Any]) -> str:
        self_drawn = self.phase == "play"
        hand = self.seats[i].cards
        if not self_drawn:
            self.seats[i].cards = [*hand, *o["cards"][:1]]
        try:
            fans = self.fans(i, self_drawn=self_drawn)
        finally:
            self.seats[i].cards = hand
        fan = sum(n for _, n in fans)
        return f"{'、'.join(k for k, _ in fans)}，共 {fan} 番"

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
                    "drawn": self.drawn
                    if show and i == self.turn and self.phase == "play"
                    else None,
                    "melds": [
                        {
                            "kind": m["kind"],
                            "tiles": list(m["tiles"])
                            if m["kind"] != "ankong" or show
                            else ["??"] * 4,
                        }
                        for m in self.melds[i]
                    ]
                    if self.melds
                    else [],
                    "river": list(self.rivers[i]) if self.rivers else [],
                    "role": s.role,
                    "last": s.last,
                    "turn": i == self.turn,
                    "score": s.score,
                }
            )
        you = next((i for i, s in enumerate(self.seats) if s.id == viewer), None)
        ld = self.last_discard
        return {
            "round_no": self.round_no,
            "phase": self.phase,
            "seats": seats,
            "wall": len(self.wall),
            "dealer": self.seats[self.dealer].id if self.started else None,
            "last_discard": {"seat": self.seats[ld[0]].id, "tile": ld[1]}
            if ld and self.phase == "claim"
            else None,
            "options": self.legal(you) if you is not None else [],
            "results": list(self.results),
            "summary": self.summary,
            "history": list(self.history[-24:]),
        }

    def brief(self, i: int) -> dict[str, str]:
        s = self.seats[i]
        lines = []
        for j, o in enumerate(self.seats):
            tag = "（你）" if j == i else ""
            role = f"，{o.role}" if o.role else ""
            ms = "、".join(
                "暗杠" if m["kind"] == "ankong" and j != i else tiles_zh(m["tiles"])
                for m in self.melds[j]
            )
            lines.append(
                f"{o.name}{tag}{role}：手上 {len(o.cards)} 张；碰杠 {ms or '没有'}；"
                f"打过 {tiles_zh(self.rivers[j])}；本桌累计 {o.score} 分"
            )
        recent = []
        for h in self.history[-12:]:
            what = h["label"] + (f" {tiles_zh(h['cards'][:1])}" if h["cards"] else "")
            recent.append(f"{h['name']}：{what}")
        if self.phase == "claim" and self.last_discard is not None:
            who, t = self.last_discard
            last = f"{self.seats[who].name}刚打出 {tile_zh(t)}，你可以选择要不要"
        elif self.claimed:
            last = "你刚碰完，打一张"
        elif self.drawn:
            last = f"你刚摸到 {tile_zh(self.drawn)}，打一张（或者胡 / 杠）"
        else:
            last = "轮到你"
        hand = tiles_zh(s.cards)
        mine = len(self.melds[i])
        return {
            "seat": s.role or "闲家",
            "hand": f"{hand}（离听牌还差 {max(0, shanten(s.cards, mine))} 步）"
            if len(s.cards) % 3 == 1
            else hand,
            "table": "\n".join(lines),
            "last": last,
            "history": "\n".join(recent) or "（刚开局）",
        }
