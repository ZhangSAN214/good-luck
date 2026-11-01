"""生活阶段与小意外（2026-10-31 拟人插件调研第 7 项；规则在 ``chat.engine.phase``）。

真人聊天里大量话题来自持续一段的事：「这周活特别多」「感冒两天了」「最近迷上一部剧」「在收拾屋子」。
每周一样的作息表加上在做的事给不出这些。思路借虚拟人生（virtual_life）的阶段排期，Python 自己写，
全程序、不调模型。

- **排期**：每天每位没有阶段、过了冷却期（``cooldown_days``）的成员，按 ``chance_per_day`` 掷一次
  （随机数由成员和日期派生，可复现），中了就从阶段表里按权重抽一种，持续 ``days`` 天里随机几天。
  同时在阶段里的人最多 ``max_concurrent`` 位（不会全员一起感冒）。
- **影响**：阶段改她的精力（``energy``）和心情（``mood``）基线；``busy`` 让她私聊回得更慢一些；
  在念头池里放一件可说的事——开始那天是新鲜事（劲儿大），中间是「最近在……」，结束后一天是
  「刚……完」。只给事实和分寸（``note``），不给固定句子。
- **迷上一样东西**：从她档案里的「最爱」挑一样，没有就从 ``things`` 里挑。

8 位同一张阶段表、同样的概率，差别只来自随机种子和她自己的档案。小病不拿来博同情、不要人陪
（§5.1 第 2 条，写在阶段的 ``note`` 里）。只用于生活模式；关心时段不受影响（那时意图卡只给关心卡）。
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.storage.repository import iso

PHASE_KEY = "member_phase"


class Phase:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.phase

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled and self.rules.kinds)

    # --- 存取 ------------------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        raw = self.rt.repo.setting(PHASE_KEY)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self.rt.repo.set_setting(PHASE_KEY, json.dumps(data, ensure_ascii=False))

    def _kind(self, name: str) -> Any:
        return next((k for k in self.rules.kinds if k.name == name), None)

    def _likes(self, member: str) -> list[str]:
        import re

        from roundtable.core.member.profile import ProfileStore

        fav = (ProfileStore(self.rt).current(member).data.get("likes") or {}).get("最爱") or []
        if isinstance(fav, str):
            fav = re.split(r"[、，,／/；;]", fav)
        return [str(x).strip() for x in fav if str(x).strip()]

    # --- 排期 ------------------------------------------------------------------------

    def current(self, member: str, at: datetime | None = None) -> dict[str, Any] | None:
        """她此刻在过的阶段（没有为 None）；今天还没掷过就先掷一次。"""
        if not self.enabled():
            return None
        at = at or self.rt.budget.now()
        data = self._load()
        mine = self._active(data.get(member), at)
        if mine is None:
            mine = self._roll(member, data, at)
        return mine

    @staticmethod
    def _active(row: Any, at: datetime) -> dict[str, Any] | None:
        if not isinstance(row, dict):
            return None
        try:
            start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
        except (KeyError, TypeError, ValueError):
            return None
        return row if start <= at < end else None

    def _roll(self, member: str, data: dict[str, Any], at: datetime) -> dict[str, Any] | None:
        r = self.rules
        zone = home_zone(self.rt)
        day = zone.local(at).date().isoformat()
        rolled = data.setdefault("_rolled", {})
        if rolled.get(member) == day:
            return None
        rolled[member] = day
        last = data.get(member)
        try:
            ended = datetime.fromisoformat(last["end"]) if isinstance(last, dict) else None
        except (KeyError, TypeError, ValueError):
            ended = None
        busy = sum(1 for m, row in data.items() if not m.startswith("_") and self._active(row, at))
        rng = random.Random(f"phase:{member}:{day}")
        cooled = ended is None or at - ended >= timedelta(days=r.cooldown_days)
        if not cooled or busy >= r.max_concurrent or rng.random() >= r.chance_per_day:
            self._save(data)
            return None
        kind = rng.choices(r.kinds, [max(k.weight, 0.0) for k in r.kinds])[0]
        days = rng.randint(min(kind.days), max(kind.days))
        start = zone.local(at).replace(hour=0, minute=0, second=0, microsecond=0)
        row = {
            "kind": kind.name,
            "start": iso(start),
            "end": iso(start + timedelta(days=days)),
        }
        if "{thing}" in kind.start + kind.note + kind.end:
            row["thing"] = rng.choice(self._likes(member) or list(r.things) or ["一样东西"])
        data[member] = row
        self._save(data)
        return row

    # --- 用在精力、回话快慢、念头上 ----------------------------------------------------

    def energy(self, member: str, at: datetime | None = None) -> float:
        row = self.current(member, at)
        kind = self._kind(row["kind"]) if row else None
        return kind.energy if kind else 0.0

    def mood(self, member: str, at: datetime | None = None) -> float:
        row = self.current(member, at)
        kind = self._kind(row["kind"]) if row else None
        return kind.mood if kind else 0.0

    def busy(self, member: str, at: datetime | None = None) -> float:
        row = self.current(member, at)
        kind = self._kind(row["kind"]) if row else None
        return kind.busy if kind else 0.0

    def thoughts(self, member: str, at: datetime | None = None) -> list[tuple[str, float]]:
        """念头池里的一件事：(文字, 劲儿)。开始那天、过程中、刚结束一天内。"""
        if not self.enabled():
            return []
        at = at or self.rt.budget.now()
        row = self.current(member, at)
        if row is None:
            last = self._load().get(member)
            if not isinstance(last, dict) or "end" not in last:
                return []
            try:
                ended = datetime.fromisoformat(last["end"])
            except ValueError:
                return []
            kind = self._kind(last.get("kind", ""))
            if kind is None or not kind.end or not (ended <= at < ended + timedelta(days=1)):
                return []
            return [(kind.end.format(thing=last.get("thing", "")), self.rules.end_strength)]
        kind = self._kind(row["kind"])
        if kind is None:
            return []
        start = datetime.fromisoformat(row["start"])
        thing = row.get("thing", "")
        if at - start < timedelta(days=1) and kind.start:
            return [(kind.start.format(thing=thing), self.rules.start_strength)]
        return [(kind.note.format(thing=thing), self.rules.during_strength)] if kind.note else []

    def listing(self) -> dict[str, dict[str, Any]]:
        """现在谁在过什么阶段（网页 / 模拟报告用）。"""
        at = self.rt.budget.now()
        out = {}
        for member, row in self._load().items():
            if member.startswith("_") or self._active(row, at) is None:
                continue
            kind = self._kind(row["kind"])
            out[member] = {
                "kind": row["kind"],
                "label": kind.label if kind else row["kind"],
                "start": row["start"],
                "end": row["end"],
            }
        return out
