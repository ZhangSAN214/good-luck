"""群聊分寸（2026-10-31 拟人插件调研第 6 项；规则在 ``chat.engine.floor``）。

真群里不是每个人都一样爱说：刚聊过这个话题的人会继续跟一阵，没参与的人不会隔半小时突然跳回来；
话多的是少数人，有人整天潜水；说多了嘴也累；一群人说个不停时，老板（用户）的话不能被淹没。
思路借天使之心（在场 / 离场、按字数扣能量）、心流、「不抢龙王」，Python 自己写，全程序、不调模型。

- **在场 / 离场**：她在这个群里 ``present_minutes`` 分钟内说过话、或被人叫到名字，就算「在场」
  （外向的久一点）。挑谁回话时在场的人更可能被挑到（× ``present_boost``）；别人正聊着而她没参与、
  也没被点名时显著性扣 ``absent_penalty``，更容易潜水（没人在聊、你开新话题时不扣）。
- **话量份额**：每天按性格分（外向、幽默）给每位一个份额（``quota_base`` 条起），今天在这个群里
  说超了就少被挑到（× ``over_weight``）、显著性扣 ``over_penalty``。
- **嘴累**：她最近说的每条话按条数和字数记「累」，按 ``tired_half_life_minutes`` 半衰；累过
  ``tired_at`` 就少被挑到、显著性扣一点。
- **别淹没你**：今天这个群里成员说的话加起来超过你说的 ``flood_ratio`` 倍（至少 ``flood_floor`` 条）
  时，没点名的消息这一轮只挑一位回。

只用于生活群；8 位同一套规则，差别只来自性格分和她们今天说了多少。点名的照常回。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone

USER = "user"
SYSTEM = "system"


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 3.0


def _when(row: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(row["created_at"])
    except (KeyError, TypeError, ValueError):
        return None


class Floor:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self._traits_cache: dict[str, dict[str, Any]] = {}

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.floor

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    def traits(self, member: str) -> dict[str, Any]:
        if member not in self._traits_cache:
            from roundtable.core.member.profile import ProfileStore  # 档案依赖较多，用到再导入

            data = ProfileStore(self.rt).current(member).data
            self._traits_cache[member] = dict((data.get("personality") or {}).get("五项") or {})
        return self._traits_cache[member]

    def rows(self, room: str) -> list[dict[str, Any]]:
        return [
            r
            for r in self.rt.repo.chat_messages(room, limit=self.rules.scan_messages)
            if r["author"] != SYSTEM and not (r.get("meta") or {}).get("recalled")
        ]

    # --- 三个量 ----------------------------------------------------------------------

    def present(
        self, member: str, rows: list[dict[str, Any]], at: datetime, name: str = ""
    ) -> bool:
        r = self.rules
        minutes = r.present_minutes + sum(
            c * (_num(self.traits(member).get(t, 3)) - 3) for t, c in r.present_traits.items()
        )
        since = at - timedelta(minutes=max(1.0, minutes))
        for row in reversed(rows):
            when = _when(row)
            if when is None or when > at:
                continue
            if when < since:
                break
            if row["author"] == member or (
                name and row["author"] != member and name in row["text"]
            ):
                return True
        return False

    def today(self, member: str, rows: list[dict[str, Any]], at: datetime) -> int:
        zone = home_zone(self.rt)
        day = zone.local(at).date()
        return sum(
            1
            for row in rows
            if row["author"] == member
            and (w := _when(row)) is not None
            and w <= at
            and zone.local(w).date() == day
        )

    def quota(self, member: str) -> float:
        r = self.rules
        k = 1 + sum(
            c * (_num(self.traits(member).get(t, 3)) - 3) for t, c in r.quota_traits.items()
        )
        return max(r.quota_min, r.quota_base * k)

    def tired(self, member: str, rows: list[dict[str, Any]], at: datetime) -> float:
        r = self.rules
        total = 0.0
        for row in rows:
            if row["author"] != member:
                continue
            when = _when(row)
            if when is None or when > at:
                continue
            minutes = (at - when).total_seconds() / 60
            cost = r.tired_per_message + r.tired_per_char * len(row["text"] or "")
            total += cost * math.pow(0.5, minutes / r.tired_half_life_minutes)
        return round(total, 3)

    def others_talking(self, member: str, rows: list[dict[str, Any]], at: datetime) -> bool:
        """这会儿群里有别的成员正在聊（present_minutes 分钟内说过话）。
        没人在聊时（你开了个新话题），没参与不算潜水的理由。"""
        since = at - timedelta(minutes=self.rules.present_minutes)
        for row in reversed(rows):
            when = _when(row)
            if when is None or when > at:
                continue
            if when < since:
                return False
            if row["author"] not in (member, USER):
                return True
        return False

    def flooded(self, rows: list[dict[str, Any]], at: datetime) -> bool:
        """今天成员说的话加起来远多于你说的。"""
        r = self.rules
        zone = home_zone(self.rt)
        day = zone.local(at).date()
        mine = theirs = 0
        for row in rows:
            when = _when(row)
            if when is None or when > at or zone.local(when).date() != day:
                continue
            if row["author"] == USER:
                mine += 1
            else:
                theirs += 1
        return theirs >= r.flood_floor and theirs > r.flood_ratio * max(1, mine)

    # --- 用在挑人和显著性上 ----------------------------------------------------------

    def weight(
        self, member: str, rows: list[dict[str, Any]], at: datetime, name: str = ""
    ) -> float:
        """挑谁回话时的权重（1 = 普通）。"""
        if not self.enabled():
            return 1.0
        r = self.rules
        w = r.present_boost if self.present(member, rows, at, name) else 1.0
        if self.today(member, rows, at) >= self.quota(member):
            w *= r.over_weight
        if self.tired(member, rows, at) >= r.tired_at:
            w *= r.tired_weight
        return w

    def penalty(self, member: str, room: str, at: datetime, name: str = "") -> float:
        """没被点名时显著性扣多少（没在场、说超了、嘴累）。"""
        if not self.enabled() or not room or room.startswith("dm:"):
            return 0.0
        r = self.rules
        rows = self.rows(room)
        out = 0.0
        if not self.present(member, rows, at, name) and self.others_talking(member, rows, at):
            out = r.absent_penalty  # 别人正聊着、她没参与：别突然插进来
        if self.today(member, rows, at) >= self.quota(member):
            out += r.over_penalty
        if self.tired(member, rows, at) >= r.tired_at:
            out += r.tired_penalty
        return round(out, 3)
