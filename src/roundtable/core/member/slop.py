"""AI 腔体检（2026-10-31 拟人插件调研第 3 项；规则在 ``chat.engine.slop``）。

8 位成员背后是大模型，会一起带出同样的「模型口头禅」（确实、说真的、我懂你、抱抱……）。
人看几十条就能认出来，但每位自己的口癖漂移只管她被分配的那几样，管不到这些。

每周（生活自检周报时）统计一次：成员在生活群、私聊、成员互聊里发的话切成 2～4 字的汉字片段，
算每位每千字用了几次；**至少 ``min_members`` 位**都高频、又远多于**你本人**（唯一的真人基线，
你的话少时按 ``user_floor_chars`` 字算）的，列进「AI 腔」；常用词（``common``）、招牌、
网络梗、短回复、你放行的（``allow``）不算。列出来的最多 ``max_active`` 个，存在 ``app_settings``
的 ``member_slop:active``，说出口前把含它的那一小句删掉（整条只有它时照发，免得不回）。

思路借 Antislop（拿模型输出和真人文本比，找高出很多倍的片段），Python 自己写；全程序、不调模型。
只用于生活模式（发言收尾），工作模式不挂载。
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.member.memes import MemeBook
from roundtable.core.storage.repository import iso

ACTIVE_KEY = "member_slop:active"
LIFE_KINDS = frozenset({"group", "dm", "pair"})
_HAN = re.compile(r"[一-鿿]+")
_CLAUSES = re.compile(r"([^，,。！!？?；;～~…\n]*[，,。！!？?；;～~…\n]*)")


def grams(text: str, lo: int = 2, hi: int = 4) -> set[str]:
    out: set[str] = set()
    for run in _HAN.findall(text or ""):
        for n in range(lo, hi + 1):
            out.update(run[i : i + n] for i in range(len(run) - n + 1))
    return out


def strip_phrases(text: str, phrases: list[str] | tuple[str, ...]) -> str:
    """把含这些片段的小句删掉；删完什么都不剩时原样返回（整条只有它时照发）。"""
    if not text or not phrases or not any(p in text for p in phrases):
        return text
    lines = []
    for line in text.split("\n"):
        parts = [p for p in _CLAUSES.findall(line) if p]
        kept = "".join(p for p in parts if not any(w in p for w in phrases)).strip()
        kept = re.sub(r"^[，,。；;！!？?～~]+", "", kept)
        if kept:
            lines.append(kept)
    return "\n".join(lines) or text


class Slop:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.slop

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    def active(self) -> list[str]:
        if not self.enabled():
            return []
        raw = self.rt.repo.setting(ACTIVE_KEY)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return []
        allow = set(self.rules.allow)
        return [p for p in data.get("phrases", []) if p not in allow]

    def _protected(self) -> set[str]:
        """不算 AI 腔的（含它或是它的一部分都不算）：你放行的、招牌、网络梗、短回复、老梗名单。
        常用词（common）另算：只有正好是它才不算，所以「说真的」不会因为含「真的」被放过。"""
        cfg = self.rt.config
        keep = set(self.rules.allow)
        for sig in cfg.personas.signatures.values():
            keep.update([*sig.tails, *sig.words, *sig.self_names])
        lingo = cfg.roundtable.chat.engine.lingo
        for words in lingo.shorts.values():
            keep.update(words)
        memes = cfg.roundtable.chat.memes
        keep.update(c.phrase for c in memes.classics)
        keep.update(memes.stale)
        keep.update(m["phrase"] for m in MemeBook(self.rt).stored())
        return keep

    def scan(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """[start, end) 的体检：返回 AI 腔候选（按严重程度排），不写库。"""
        r = self.rules
        kinds = {room["id"]: room["kind"] for room in self.rt.repo.chat_rooms()}
        counts: dict[str, Counter] = defaultdict(Counter)
        chars: Counter = Counter()
        for msg in self.rt.repo.chat_messages_between(iso(start), iso(end)):
            if kinds.get(msg["room"]) not in LIFE_KINDS or msg["author"] in ("user", "system"):
                continue
            text = msg["text"] or ""
            chars[msg["author"]] += sum(len(run) for run in _HAN.findall(text))
            counts[msg["author"]].update(grams(text))
        mine: Counter = Counter()
        user_chars = 0
        since = iso(end - timedelta(days=r.user_days))
        for msg in self.rt.repo.chat_messages_between(since, iso(end)):
            if kinds.get(msg["room"]) in LIFE_KINDS and msg["author"] == "user":
                user_chars += sum(len(run) for run in _HAN.findall(msg["text"] or ""))
                mine.update(grams(msg["text"] or ""))
        base = max(user_chars, r.user_floor_chars)
        common = set(r.common)
        protected = [p for p in self._protected() if len(p) >= 2]
        found: list[dict[str, Any]] = []
        every = set().union(*(c.keys() for c in counts.values())) if counts else set()
        for g in every:
            hot = []
            for member, c in counts.items():
                if c[g] >= r.min_count and chars[member]:
                    rate = c[g] * 1000 / chars[member]
                    if rate >= r.min_rate:
                        hot.append((member, rate))
            if len(hot) < r.min_members:
                continue
            if g in common or any(g in p or p in g for p in protected):
                continue  # 常用词；招牌、梗、短回复、你放行的（含它或是它的一部分）
            rate = sum(x for _, x in hot) / len(hot)
            user_rate = mine[g] * 1000 / base
            if rate < r.ratio * (user_rate + r.smooth):
                continue
            total = sum(c[g] for c in counts.values())
            found.append(
                {
                    "phrase": g,
                    "members": sorted(m for m, _ in hot),
                    "rate": round(rate, 2),
                    "user_rate": round(user_rate, 2),
                    "count": total,
                }
            )
        found.sort(key=lambda f: (-len(f["phrase"]), -f["count"]))
        kept: list[dict[str, Any]] = []
        for f in found:  # 长片段盖住它里面的短片段（次数差不多时只留长的）
            if any(f["phrase"] in k["phrase"] and k["count"] >= 0.7 * f["count"] for k in kept):
                continue
            kept.append(f)
        kept.sort(key=lambda f: (-len(f["members"]) * f["rate"], f["phrase"]))
        return kept

    def weekly(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """体检一次，把前 max_active 个存起来（发言收尾时删掉）。返回存下的。"""
        if not self.enabled():
            return []
        top = self.scan(start, end)[: self.rules.max_active]
        self.rt.repo.set_setting(
            ACTIVE_KEY,
            json.dumps(
                {"week": iso(start), "phrases": [f["phrase"] for f in top], "found": top},
                ensure_ascii=False,
            ),
        )
        return top
