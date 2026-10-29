"""一周的经历（P6 成长的材料）：程序把这位成员上一周的日记、记下的事、用户对她说过的话
整理成带编号的几十行，每条成长改动都要引用其中的编号和原话。

- 日记（P2 夜间整理写的 ``day`` 层）按句拆开，一句一行，方便照抄引用。
- 记下的事：这一周的记忆件（含工作领域的经历，生活只读自己的工作记录由 P2 负责）。
- 用户说的话：这一周用户在她在场的私聊、群聊里说过的最近几句（工作群不算）。
- 每行标「是否受用户影响」：用户说的话，以及提到用户的日记和记忆件。
- 只读这些表，不写任何东西；所有成员同一套规则（中立）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from roundtable.core.storage.repository import iso

DIARY, ITEM, USER, WORK = "diary", "item", "user", "work"
SOURCE_NAMES = {DIARY: "日记", ITEM: "记下的事", USER: "用户说", WORK: "工作"}
SENTENCE = re.compile(r"[^。！？!?\n]+[。！？!?]?")
NONE = "（没有）"


@dataclass(frozen=True)
class Line:
    n: int  # 编号（从 1 起）
    source: str  # diary / item / user / work
    day: str
    text: str
    user: bool  # 受用户影响（用户说的话、提到用户的经历）


@dataclass
class Week:
    member: str
    week: str  # 2026-W43
    start: date  # 周一
    lines: list[Line] = field(default_factory=list)

    @property
    def days(self) -> int:
        """有经历的天数。"""
        return len({ln.day for ln in self.lines})

    def line(self, n: int) -> Line | None:
        return self.lines[n - 1] if 1 <= n <= len(self.lines) else None

    def text(self) -> str:
        return (
            "\n".join(
                f"{ln.n}. [{ln.day[5:]} {SOURCE_NAMES[ln.source]}] {ln.text}" for ln in self.lines
            )
            or NONE
        )


def week_id(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def monday(day: date) -> date:
    return day - timedelta(days=day.isoweekday() - 1)


def _window(start: date, days: int, tz: tzinfo | None) -> tuple[str, str]:
    a = datetime.combine(start, time(), tz)
    b = datetime.combine(start + timedelta(days=days), time(), tz)
    return iso(a), iso(b)


def _cut(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def build(
    rt: Any,
    member: str,
    start: date,
    tz: tzinfo | None,
    *,
    user_label: str = "用户",
    user_author: str = "user",
) -> Week:
    """这位成员从周一 start 起 7 天的经历（按天、来源排好，编号从 1 起，总长不超过上限）。"""
    rules = rt.config.roundtable.chat.engine.growth
    repo = rt.repo
    days = {(start + timedelta(days=i)).isoformat() for i in range(7)}
    end = (start + timedelta(days=7)).isoformat()
    raw: list[tuple[str, str, str, bool]] = []  # (day, source, text, user)
    for d in repo.memories(member, layer="day", limit=60):
        if d["period"] not in days:
            continue
        for s in SENTENCE.findall(d["text"]):
            s = s.strip()
            if len(s) >= 4:
                raw.append((d["period"], DIARY, s, user_label in s))
    for it in repo.memory_items(member, since_day=start.isoformat(), current=False, limit=500):
        if it["day"] >= end:
            continue
        source = WORK if it["domain"] == "工作" else ITEM
        raw.append((it["day"], source, it["text"], user_label in it["text"]))
    a, b = _window(start, 7, tz)
    rooms = {r["id"]: r for r in repo.chat_rooms()}
    said = []
    for msg in repo.chat_messages_between(a, b):
        room = rooms.get(msg["room"])
        if msg["author"] != user_author or room is None or room["kind"] == "work":
            continue
        if room["members"] and member not in room["members"]:
            continue
        at = datetime.fromisoformat(msg["created_at"]).astimezone(tz)
        said.append((at.date().isoformat(), USER, msg["text"], True))
    if rules.user_lines:
        raw += said[-rules.user_lines :]
    order = {DIARY: 0, ITEM: 1, WORK: 2, USER: 3}
    raw.sort(key=lambda r: (r[0], order[r[1]]))
    week = Week(member, week_id(start), start)
    room = rules.material_chars
    for day, source, text, user in raw:
        text = _cut(text, rules.line_chars)
        if not text:
            continue
        room -= len(text) + 12
        if room < 0:
            break
        week.lines.append(Line(len(week.lines) + 1, source, day, text, user))
    return week
