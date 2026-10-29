"""召回：要调模型时，从记忆件里挑这次要带的几件（P2；不调用模型，所有成员同一套规则）。

1. **候选**只看几百件：全文索引按当前对话找 ``candidates`` 件 + 最近 ``recent`` 件 + 重要的事实。
   记忆从 100 条涨到 10 万条，每次读的行数和注入的字数都不变（有测试）。
2. **打分** = 鲜活度 × (重要度 / 3 + 相关度) × (1 + 合并次数的对数 / 10)。
   鲜活度 = ``max(保底, 0.5 ^ (过去的天数 / 半衰期))``
   （借 dsh layered-memory 的保底思路，自己重写）；
   事实（约定、偏好、用户写的）不变淡。相关度：和当前对话共有的片段，按在候选里的罕见程度加权。
3. **同一次对话不重复注入**：同一会话里、间隔不超过 ``repeat_gap_minutes`` 的几次调用算一次对话，
   已经带过的件不再带（她当时的回答已经在聊天记录里了），重要度 3 的事实除外。
4. **联想**：从分数最高的几件顺着联想网扩散，多带 ``assoc_max`` 件「想起」；一起被想起的件互相加强。
5. **脑子里挂着的事**放最前（最多 ``hanging_max`` 件）。全部经上下文组装器装进
   ``context_chars`` 字（8 位成员同一个预算）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from roundtable.core.member.context import Piece, assemble
from roundtable.core.member.memory.links import LinkNet
from roundtable.core.member.memory.store import FACT, MemoryStore, query_terms
from roundtable.core.recall import _distinct, grams

NONE = "（暂无）"
FACT_CANDIDATES = 50
QUERY_TAIL = 120  # 只用当前对话最后这些字找候选（最新的话最相关）
WORD_LIMIT = 8
# 同一次对话已经带过的件：{数据库连接: {(成员, 会话): (上次时间, {件 id})}}，只在内存里
_SEEN: dict[int, dict[tuple[str, str], tuple[datetime, set[int]]]] = {}


@dataclass
class Scored:
    row: dict[str, Any]
    score: float = 0.0
    hits: list[str] = field(default_factory=list)
    assoc: bool = False

    @property
    def id(self) -> int:
        return self.row["id"]


@dataclass
class Recollection:
    text: str
    chosen: list[Scored]
    hanging: list[dict[str, Any]]
    candidates: int


def freshness(day: str, today: date, half_life_days: float, floor: float) -> float:
    try:
        age = max((today - date.fromisoformat(day)).days, 0)
    except ValueError:
        return 1.0
    return max(floor, 0.5 ** (age / half_life_days))


def label(s: Scored) -> str:
    if s.assoc:
        return "想起"
    if s.row["kind"] == FACT:
        return "长期"
    try:
        return date.fromisoformat(s.row["day"]).strftime("%m-%d")
    except ValueError:
        return ""


def line(s: Scored) -> str:
    text = s.row["tone"] or s.row["text"]
    tag = label(s)
    return f"【{tag}】{text}" if tag else text


def why(s: Scored) -> str:
    if s.assoc:
        return "顺着联想想起"
    if s.hits:
        return "提到了" + "、".join(f"『{h}』" for h in s.hits)
    if s.row["importance"] == 3:
        return "标了重要，总是记得"
    if s.row["kind"] == FACT:
        return "长期记住的"
    return "最近的事"


class Recaller:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.store = MemoryStore(rt)
        self.links = LinkNet(rt)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.memory

    def _seen(self, member: str, conversation: str | None, now: datetime) -> set[int]:
        if not conversation:
            return set()
        book = _SEEN.setdefault(id(self.rt.repo.conn), {})
        last = book.get((member, conversation))
        if last is None:
            return set()
        gap = (now - last[0]).total_seconds() / 60
        return set(last[1]) if gap <= self.rules.repeat_gap_minutes else set()

    def _remember_seen(
        self, member: str, conversation: str | None, now: datetime, ids: set[int]
    ) -> None:
        if conversation:
            book = _SEEN.setdefault(id(self.rt.repo.conn), {})
            book[(member, conversation)] = (now, ids)

    def candidates(self, member: str, query: str) -> list[dict[str, Any]]:
        repo, r = self.rt.repo, self.rules
        recent_talk = query[-QUERY_TAIL:]
        ids = repo.search_memory_items(member, query_terms(recent_talk), r.candidates)
        if len(ids) < r.candidates:  # 两字词（拉面、考试）三字片段查不到，补查
            words = query_terms(recent_talk, WORD_LIMIT, size=2)
            more = repo.like_memory_items(member, words, r.candidates - len(ids))
            ids += [i for i in more if i not in ids]
        rows = {row["id"]: row for row in repo.memory_items_by_ids(ids)}
        for row in repo.memory_items(member, limit=r.recent):
            rows.setdefault(row["id"], row)
        for row in repo.memory_items(member, kind=FACT, min_importance=2, limit=FACT_CANDIDATES):
            rows.setdefault(row["id"], row)
        return [row for row in rows.values() if row["superseded_by"] is None]

    def score(self, rows: list[dict[str, Any]], query: str, today: date) -> list[Scored]:
        r = self.rules
        bags = [grams(row["text"]) for row in rows]
        df: dict[str, int] = {}
        for bag in bags:
            for g in bag:
                df[g] = df.get(g, 0) + 1
        n = len(rows)
        q = grams(query)
        out = []
        for row, bag in zip(rows, bags, strict=True):
            shared = [g for g in bag & q if df[g] < n or n == 1]
            rel = sum(math.log((n + 1) / df[g]) * (len(g) - 1) for g in shared)
            fresh = (
                1.0
                if row["kind"] == FACT
                else freshness(row["day"], today, r.half_life_days, r.freshness_floor)
            )
            boost = 1 + math.log(max(row["hits"], 1)) / 10
            s = Scored(row, fresh * (row["importance"] / 3 + rel / 4) * boost)
            s.hits = _distinct(shared)[:3]
            out.append(s)
        return sorted(out, key=lambda s: (-s.score, -s.id))

    def recall(
        self,
        member: str,
        query: str,
        *,
        today: date,
        now: datetime,
        conversation: str | None = None,
        budget_chars: int | None = None,
    ) -> Recollection:
        r = self.rules
        budget = r.context_chars if budget_chars is None else budget_chars
        rows = self.candidates(member, query)
        seen = self._seen(member, conversation, now)
        scored = [
            s
            for s in self.score(rows, query, today)
            if s.id not in seen or s.row["importance"] == 3
        ]
        always = [s for s in scored if s.row["importance"] == 3 and s.row["kind"] == FACT]
        always = always[: max(r.inject_max // 2, 1)]
        rest = [s for s in scored if s not in always]
        chosen = always + rest[: max(r.inject_max - len(always), 0)]
        seeds = {s.id: s.score for s in sorted(chosen, key=lambda s: -s.score)[: r.links.seeds]}
        top = max(seeds.values(), default=0.0) or 1.0
        spread = self.links.spread(member, {k: v / top for k, v in seeds.items()})
        spread = {k: v for k, v in spread.items() if k not in seen}
        assoc_ids = sorted(spread, key=lambda k: (-spread[k], -k))[: r.links.assoc_max]
        assoc_rows = [row for row in self.rt.repo.memory_items_by_ids(assoc_ids)]
        assoc = [
            Scored(row, spread[row["id"]], assoc=True)
            for row in sorted(assoc_rows, key=lambda x: -spread[x["id"]])
            if row["superseded_by"] is None
        ]
        hanging = self.rt.repo.memory_hanging(member)[: r.hanging_max]
        pieces = [Piece("hanging", f"【挂着】{h['text']}", fixed=True) for h in hanging]
        ordered = sorted(chosen, key=lambda s: (s.row["kind"] != FACT, s.row["day"], s.id))
        pieces += [Piece("memory", line(s), score=1000 - i) for i, s in enumerate(ordered)]
        pieces += [Piece("assoc", line(s), score=-1 - i) for i, s in enumerate(assoc)]
        ctx = assemble(pieces, budget, per_source=max(r.inject_max, 1), min_piece_chars=20)
        used_text = {p.text for p in ctx.used}
        final = [
            s
            for s in ordered + assoc
            if line(s) in used_text
            or any(u.endswith("…") and line(s).startswith(u[:-1]) for u in used_text)
        ]
        text = ctx.text or NONE
        if final:
            self.rt.repo.mark_items_recalled({s.id: why(s) for s in final})
            self.links.strengthen(member, [s.id for s in final])
            self.store.train(
                member,
                "recall",
                {
                    "query": query[-300:],
                    "conversation": conversation or "",
                    "candidates": len(rows),
                    "chosen": [[s.id, round(s.score, 4), s.assoc] for s in final],
                },
            )
        self._remember_seen(member, conversation, now, seen | {s.id for s in final})
        return Recollection(text, final, hanging, len(rows))
