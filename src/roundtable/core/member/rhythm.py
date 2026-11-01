"""作息与回话节奏（docs/BRAIN.md「作息与回话节奏」；2026-10-30 用户定）。

真人不会半夜三点秒回。成员按公寓作息睡觉时（主时区的钟点），你发来的消息：

- **大多数时候睡着没看到**：私聊里记下来，等她醒了再回（意图卡里写「你刚醒，才看到」）；
  群里没点她的名就不回。
- **有时被吵醒**：点名、关系近、连着发几条更容易吵醒；吵醒了迷迷糊糊地回，心情不好时可以嘟囔
  抱怨一两句（「大半夜的……」），但不真生气、不翻旧账；对方有急事就清醒过来认真回。
- **对方说出让人担心的话**（看懂语境判成「状态很不好」）：不管睡没睡，立刻回。

全程序、不调模型；只用于生活群和私聊，工作群照常。8 位同一套规则，差别只来自作息、心情和关系。
等着回的消息存在 ``app_settings`` 的 ``member_rhythm:pending``，只有本模块写。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.office.apartment import schedule
from roundtable.core.storage.repository import iso

PENDING_KEY = "member_rhythm:pending"
NOW, LATER, WOKEN = "now", "later", "woken"
USER = "user"


@dataclass(frozen=True)
class Gate:
    """这条消息她怎么处理：now 照常回 / later 睡着没看到 / woken 被吵醒；note 写进意图卡。"""

    kind: str
    note: str = ""


class Rhythm:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.rhythm

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    def asleep(self, member: str, at: datetime) -> bool:
        local = home_zone(self.rt).local(at)
        return schedule(member, local.hour, local.weekday())[1] == "sleep"

    # --- 你发来消息时 ----------------------------------------------------------------

    def gate(
        self,
        member: str,
        *,
        room: str,
        text: str,
        meta: dict[str, Any] | None,
        named: bool,
        msg_id: int,
        at: datetime,
    ) -> Gate:
        if not self.enabled() or not self.asleep(member, at):
            return Gate(NOW)
        from roundtable.core.member.lingo import Lingo  # lingo 不依赖本模块

        gist = Lingo(self.rt).gist(text, meta)
        if gist is not None and gist.kind == "crisis":
            return Gate(NOW)  # 让人担心的话：睡着也立刻回
        r = self.rules
        chance = r.wake_base + (r.wake_named if named else 0.0)
        chance += r.wake_close * self._care(member, at)
        chance += r.wake_repeat * min(self._burst(room, at) - 1, 3)
        roll = random.Random(f"rhythm:{member}:{msg_id}").random()
        if roll < min(chance, r.wake_max):
            grumpy = self._mood(member, at) < r.grumpy_below
            return Gate(WOKEN, r.woken_grumpy if grumpy else r.woken)
        return Gate(LATER)

    def _care(self, member: str, at: datetime) -> float:
        from roundtable.core.member.love import Heart  # love 不依赖本模块

        try:
            return float(Heart(self.rt).care(member, USER, at))
        except Exception:  # 感情没开或出错：当作普通关系
            return 0.0

    def _mood(self, member: str, at: datetime) -> float:
        from roundtable.core.member.state import StatePlugin  # state 不依赖本模块

        try:
            return float(StatePlugin(self.rt).mood_of(member, at))
        except Exception:
            return 0.0

    def _burst(self, room: str, at: datetime) -> int:
        """最近十分钟你在这里连着发了几条（连发更容易把人吵醒）。"""
        since = iso(at - timedelta(minutes=10))
        rows = self.rt.repo.chat_messages(room, limit=20)
        return max(1, sum(1 for m in rows if m["author"] == USER and m["created_at"] >= since))

    # --- 睡着没看到的：等她醒了再回 ---------------------------------------------------------

    def pending(self) -> list[dict[str, Any]]:
        raw = self.rt.repo.setting(PENDING_KEY)
        return json.loads(raw) if raw else []

    def defer(self, member: str, room: str, msg_id: int, at: datetime) -> None:
        rows = [p for p in self.pending() if not (p["member"] == member and p["room"] == room)]
        first = next(
            (p["since"] for p in self.pending() if p["member"] == member and p["room"] == room),
            iso(at),
        )
        rows.append({"member": member, "room": room, "msg": msg_id, "since": first})
        self.rt.repo.set_setting(PENDING_KEY, json.dumps(rows[-50:]))

    def due(self, at: datetime) -> list[dict[str, Any]]:
        """醒了的人该回的消息（每人每个会话一条：回你最新的那条）；太久的直接放下。从待回里拿走。"""
        if not self.enabled():
            return []
        keep, out = [], []
        oldest = at - timedelta(hours=self.rules.keep_hours)
        for p in self.pending():
            if datetime.fromisoformat(p["since"]) < oldest:
                continue
            (keep if self.asleep(p["member"], at) else out).append(p)
        self.rt.repo.set_setting(PENDING_KEY, json.dumps(keep))
        return out

    def late_note(self, entry: dict[str, Any]) -> str:
        local = home_zone(self.rt).local(datetime.fromisoformat(entry["since"]))
        return self.rules.late.format(when=f"{local.hour}:{local.minute:02d}")
