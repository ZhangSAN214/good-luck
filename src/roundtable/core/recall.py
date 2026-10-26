"""半衰期记忆的召回（docs/CHAT.md G3.1，``chat.memory.recall: half_life`` 时启用；默认关闭）。

思路借自 Yuuyuko-uu「鱼的记忆」，自己重写，没有复制代码：

- 每条记忆拆成若干「件」：长期档案按行、日记忆按行（整理时每行一件事）；
  周 / 月总结和随手记各算一件。
- **事实**不变淡：长期档案、用户自己写或改过的、整理时标了「★」的（约定、偏好、硬性要求）。
  其余是**发生过的事**，鲜活度 ``0.5 ^ (过去的天数 / half_life_days)``，只排得更靠后，不删。
- 打分 = 鲜活度 × (重要度 / 3 + 相关度)。相关度由程序算、不调用模型：去掉标点后切 2～4 字片段，
  和当前对话共有的片段按罕见程度加权（在很多条记忆里都出现的片段几乎不算）。
- 标了「★」的事实永远带；其余按分数取前几件，总共最多 ``inject_max`` 件，
  再受 ``context_chars`` 约束。
- 记下每条记忆最近一次被想起的时间和原因（命中了哪些词），记忆页可以看到。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

STAR = "★"
_NOISE = re.compile(r"[\W_]+")
_LABELS = {"profile": "长期", "note": "记下的", "week": "那周", "month": "那个月"}


@dataclass
class Piece:
    memory_id: int
    text: str
    fact: bool
    importance: int  # 1～3
    when: date | None
    label: str
    score: float = 0.0
    hits: list[str] = field(default_factory=list)


def grams(text: str, sizes: tuple[int, ...] = (2, 3, 4)) -> set[str]:
    """去掉标点空白后的 2～4 字片段（中文不需要分词）。"""
    t = _NOISE.sub("", (text or "").lower())
    return {t[i : i + n] for n in sizes for i in range(len(t) - n + 1)}


def freshness(when: date | None, today: date, half_life_days: float) -> float:
    if when is None:
        return 1.0
    age = max((today - when).days, 0)
    return 0.5 ** (age / half_life_days)


def _period_date(period: str, layer: str) -> date | None:
    try:
        if layer == "day":
            return date.fromisoformat(period)
        if layer == "week":
            year, week = period.split("-W")
            return date.fromisocalendar(int(year), int(week), 7)
        if layer == "month":
            year, month = period.split("-")
            return date(int(year), int(month), 28)
    except ValueError:
        return None
    return None


def pieces(rows: list[dict[str, Any]]) -> list[Piece]:
    """把一位成员的记忆行拆成召回用的「件」。"""
    out: list[Piece] = []
    for r in rows:
        layer, user = r["layer"], r["source"] == "user"
        when = _period_date(r["period"], layer) or datetime.fromisoformat(r["created_at"]).date()
        lines = r["text"].splitlines() if layer in ("profile", "day") else [r["text"]]
        for line in (ln.strip() for ln in lines):
            star = line.startswith(STAR)
            line = line.lstrip(STAR).strip()
            if not line:
                continue
            fact = layer == "profile" or user or star
            label = _LABELS.get(layer) or (when.strftime("%m-%d") if when else "")
            out.append(
                Piece(
                    memory_id=r["id"],
                    text=line,
                    fact=fact,
                    importance=3 if star else (2 if fact else 1),
                    when=None if fact else when,
                    label="长期" if fact and layer != "note" else label,
                )
            )
    return out


def _distinct(hits: list[str]) -> list[str]:
    """命中的片段去掉被更长片段包含的，最长的在前。"""
    kept: list[str] = []
    for g in sorted(hits, key=len, reverse=True):
        if not any(g in k for k in kept):
            kept.append(g)
    return kept


def recall(
    rows: list[dict[str, Any]],
    query: str,
    today: date,
    *,
    half_life_days: float,
    inject_max: int,
) -> list[Piece]:
    """挑出这次要带上的记忆件：★ 事实全带，其余按 鲜活度 × (重要度 / 3 + 相关度) 排序。"""
    items = pieces(rows)
    if not items:
        return []
    bags = [grams(p.text) for p in items]
    df: dict[str, int] = {}
    for bag in bags:
        for g in bag:
            df[g] = df.get(g, 0) + 1
    n = len(items)
    q = grams(query)
    for p, bag in zip(items, bags, strict=True):
        shared = [g for g in bag & q if df[g] < n or n == 1]  # 每条都有的片段不算命中
        rel = sum(math.log((n + 1) / df[g]) * (len(g) - 1) for g in shared)
        p.hits = _distinct(shared)[:3]
        p.score = freshness(p.when, today, half_life_days) * (p.importance / 3 + rel / 4)
    always = [p for p in items if p.importance == 3]
    rest = sorted((p for p in items if p.importance < 3), key=lambda p: p.score, reverse=True)
    chosen = always + rest[: max(inject_max - len(always), 0)]
    return sorted(chosen, key=lambda p: (not p.fact, p.when or date.min))


def render(chosen: list[Piece]) -> str:
    return "\n".join(f"【{p.label}】{p.text}" if p.label else p.text for p in chosen)


def recall_notes(chosen: list[Piece]) -> dict[int, str]:
    """每条记忆这次被想起的原因（同一条的几件合并）。"""
    notes: dict[int, list[str]] = {}
    for p in chosen:
        if p.hits:
            why = "提到了" + "、".join(f"『{h}』" for h in p.hits)
        elif p.importance == 3:
            why = "标了重要，总是记得"
        elif p.fact:
            why = "长期记住的"
        else:
            why = "最近的事"
        notes.setdefault(p.memory_id, [])
        if why not in notes[p.memory_id]:
            notes[p.memory_id].append(why)
    return {mid: "；".join(w) for mid, w in notes.items()}
