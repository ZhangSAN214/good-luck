"""翻日记（P2）：生活模式里成员自己翻以前的日记和记忆件（原始记录一直保留，平时只带几件，想细查时翻）。

- **只在生活模式**（私聊、生活群）：工作模式调用直接报错（``require_life``）。
- 每位成员每天最多翻 ``diary.daily_reads`` 次（本地日，所有成员相同）；翻一次算一次，翻不到也算。
- 查法：日期（``2026-10-21`` / ``10-21``）直接找那天的日记；其余按关键词找日记和记忆件。
  不调用模型，每次最多 ``max_hits`` 段、``result_chars`` 字。
- 成员在回复里写 ``<tool_call name="read_diary">关键词或日期</tool_call>``
  （和工作模式工具同一种文字协议），
  程序翻好放进 ``<diary>`` 标签交回，她再接着回复（多一次调用，只在她真的翻时发生）。
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any

from roundtable.core.member.memory.store import query_terms
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.storage.repository import iso

TOOL = "read_diary"
_DATE = re.compile(r"(?:(\d{4})[-/年])?(\d{1,2})[-/月](\d{1,2})日?")
_SPLIT = re.compile(r"[\s,，、;；。.!！?？]+")


def _local_day_window(today: date) -> tuple[str, str]:
    start = datetime.combine(today, time(0)).astimezone()
    return iso(start), iso(start + timedelta(days=1))


class DiaryTool:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.memory.diary

    def _today(self) -> date:
        return self.rt.budget.now().astimezone().date()

    def remaining(self, member: str, mode: Mode | str = Mode.LIFE) -> int:
        require_life(mode)
        mem = self.rt.config.roundtable.chat.memory
        if not (mem.enabled and self.rules.enabled):
            return 0
        start, end = _local_day_window(self._today())
        used = self.rt.repo.diary_reads_between(member, start, end)
        return max(self.rules.daily_reads - used, 0)

    def find(self, member: str, query: str) -> list[str]:
        """不计次数的查找（用户在网页上翻也用它）。"""
        repo, r = self.rt.repo, self.rules
        query = " ".join((query or "").split())[:60]
        out: list[str] = []
        m = _DATE.search(query)
        if m:
            year = int(m.group(1)) if m.group(1) else self._today().year
            try:
                day = date(year, int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                day = ""
            for d in repo.memories(member, layer="day", limit=2000):
                if d["period"] == day:
                    out.append(f"【{day} 的日记】{d['text']}")
            for it in repo.memory_items(member, day=day, current=False, limit=r.max_hits):
                out.append(f"【{day}】{it['text']}")
        words = [w for w in _SPLIT.split(_DATE.sub(" ", query)) if len(w) >= 2]
        for d in repo.search_diaries(member, words, r.max_hits):
            out.append(f"【{d['period']} 的日记】{d['text']}")
        ids = repo.search_memory_items(member, query_terms(" ".join(words)), r.max_hits)
        for it in sorted(repo.memory_items_by_ids(ids), key=lambda x: x["day"], reverse=True):
            out.append(f"【{it['day']}】{it['text']}")
        seen: list[str] = []
        for t in out:
            if t not in seen:
                seen.append(t)
        return seen[: r.max_hits]

    def read(self, member: str, query: str, mode: Mode | str = Mode.LIFE) -> str | None:
        """成员翻一次日记。次数用完返回 None；翻不到返回说明。"""
        if self.remaining(member, mode) <= 0:
            return None
        found = self.find(member, query)
        self.rt.repo.add_diary_read(member, iso(self.rt.budget.now()), query[:60], len(found))
        text = "\n".join(found) or "没翻到相关的记录。"
        limit = self.rules.result_chars
        return text if len(text) <= limit else text[: limit - 1] + "…"
