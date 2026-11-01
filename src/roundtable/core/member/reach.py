"""主动找你（docs/BRAIN.md「主动找你」；2026-10-30 用户定：可以按她的心情主动找）。

以前成员只在你说话后回应。现在她醒着、你也不在睡觉的钟点里，她可能先开口——但要有真事由：

- **心情**：心情很低又在乎你，想找你说说话；心情很好，想跟你分享。
- **新鲜事**：自己身上刚发生、还没跟人讲的事（大脑分区记下的）。
- **挂着的事**：之前听你说过、一直惦记的事（「你组会怎么样了」）。
- **想念**：好几天没聊、又挺在乎你。

边界（CLAUDE.md §5.1 第 2 条）：她上次找你你还没回，就不再找（只找一次，不追）；
你几小时内刚和她聊过不找；每人隔几天才可能找一次，全体每天有上限；你的深夜不找；话里不拿伤心要挟、不怪你不回。全程序挑时机，
开口那一句才调用一次模型（和私聊回话同一套提示词）。8 位同一套规则，差别只来自心情、关系和经历。
状态存在 ``app_settings`` 的 ``member_reach:state``，只有本模块写。
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.storage.repository import iso

STATE_KEY = "member_reach:state"
USER = "user"


@dataclass(frozen=True)
class Urge:
    member: str
    reason: str  # mood_low / mood_high / news / hanging / longing
    note: str  # 写进意图卡的一句
    strength: float
    tag: str = ""  # 话头本的标记：真找了就划掉


class Reach:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.reach

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    def _load(self) -> dict[str, Any]:
        raw = self.rt.repo.setting(STATE_KEY)
        return json.loads(raw) if raw else {"members": {}, "days": {}}

    def _save(self, data: dict[str, Any]) -> None:
        data["days"] = dict(list(data.get("days", {}).items())[-7:])
        self.rt.repo.set_setting(STATE_KEY, json.dumps(data, ensure_ascii=False))

    # --- 时机 -----------------------------------------------------------------------

    def quiet_now(self, at: datetime) -> bool:
        """你的深夜（主时区钟点）：不找。"""
        start, end = self.rules.quiet_hours
        hour = home_zone(self.rt).local(at).hour
        return hour >= start or hour < end if start > end else start <= hour < end

    def _last_user(self, room: str) -> dict[str, Any] | None:
        rows = [r for r in self.rt.repo.chat_messages(room, limit=40) if r["author"] == USER]
        return rows[-1] if rows else None

    def blocked(self, member: str, room: str, at: datetime) -> bool:
        """这会儿不能找：上次找了你还没回、隔得太近、你刚和她聊过、今天全体已经找够了。"""
        data, r = self._load(), self.rules
        mine = data["members"].get(member) or {}
        last_user = self._last_user(room)
        if mine.get("msg") and not (last_user and last_user["id"] > mine["msg"]):
            return True  # 上次找你你还没回：不追
        if mine.get("at") and at - datetime.fromisoformat(mine["at"]) < timedelta(days=r.gap_days):
            return True
        if last_user and at - datetime.fromisoformat(last_user["created_at"]) < timedelta(
            hours=r.after_user_hours
        ):
            return True
        today = home_zone(self.rt).local(at).date().isoformat()
        return data["days"].get(today, 0) >= r.per_day

    # --- 想不想找、为什么 ------------------------------------------------------------

    def urge(self, member: str, room: str, at: datetime) -> Urge | None:
        r = self.rules
        care, mood = self._care(member, at), self._mood(member, at)
        options: list[Urge] = []
        if mood <= r.mood_low and care >= r.care_min:
            options.append(Urge(member, "mood_low", r.notes["mood_low"], min(1.0, -mood)))
        if mood >= r.mood_high:
            options.append(Urge(member, "mood_high", r.notes["mood_high"], min(1.0, mood)))
        news = self._news(member, at)
        if news:
            options.append(
                Urge(member, "news", r.notes["news"].format(what=news[0]), float(news[1]))
            )
        hanging = self.rt.repo.memory_hanging(member)
        if hanging:
            text = str(hanging[-1]["text"])
            options.append(Urge(member, "hanging", r.notes["hanging"].format(what=text), 0.6))
        from roundtable.core.member.hooks import Hooks  # 话头本不依赖本模块

        plans = Hooks(self.rt).due(member, at)
        if plans:  # 他说过要做的事那天过了：想问问后来怎样（11-01 你说没有聊天欲望）
            note = r.notes["hook"].format(what=plans[0]["quote"])
            options.append(Urge(member, "hook", note, 0.7, f"hook:p:{plans[0]['id']}"))
        last_user = self._last_user(room)
        if last_user and care >= r.care_min:
            days = (at - datetime.fromisoformat(last_user["created_at"])).total_seconds() / 86400
            if days >= r.longing_days:
                strength = min(1.0, days / (r.longing_days * 3)) * care
                options.append(Urge(member, "longing", r.notes["longing"], strength))
        if not options:
            return None
        return max(options, key=lambda u: u.strength)

    def _care(self, member: str, at: datetime) -> float:
        from roundtable.core.member.love import Heart  # love 不依赖本模块

        try:
            return float(Heart(self.rt).care(member, USER, at))
        except Exception:
            return 0.0

    def _mood(self, member: str, at: datetime) -> float:
        from roundtable.core.member.state import StatePlugin  # state 不依赖本模块

        try:
            return float(StatePlugin(self.rt).mood_of(member, at))
        except Exception:
            return 0.0

    def _news(self, member: str, at: datetime) -> tuple[str, float] | None:
        from roundtable.core.member.brain import Brain  # brain 不依赖本模块

        try:
            news = Brain(self.rt).snapshot(member, at)["news"]
        except Exception:
            return None
        strong = [n for n in news if float(n.get("strength", 0)) >= self.rules.news_min]
        return (str(strong[-1]["text"]), float(strong[-1]["strength"])) if strong else None

    def pick(self, members: list[str], room_of: Any, at: datetime) -> Urge | None:
        """这次推进谁想主动找你（最多一位）。每位每次推进掷一次，概率 = 每天的基础概率 × 想找的劲儿
        ÷ 一天推进几次。"""
        if not self.enabled() or self.quiet_now(at):
            return None
        from roundtable.core.member.rhythm import Rhythm  # rhythm 不依赖本模块

        rhythm, r = Rhythm(self.rt), self.rules
        ticks = max(1, round(24 * 60 / self.rt.config.roundtable.chat.life.tick_minutes))
        slot = at.replace(second=0, microsecond=0).isoformat()
        for member in members:
            room = room_of(member)
            if rhythm.asleep(member, at) or self.blocked(member, room, at):
                continue
            urge = self.urge(member, room, at)
            if urge is None:
                continue
            roll = random.Random(f"reach:{member}:{slot}").random()
            if roll < r.per_day_chance * urge.strength * self.outgoing(member) / ticks:
                return urge
        return None

    def outgoing(self, member: str) -> float:
        """外向的更爱先开口（11-01 你说没有聊天欲望）：外向分每高 1 分 × (1 + extrovert)。"""
        from roundtable.core.member.profile import ProfileStore  # 档案依赖较多，用到再导入

        try:
            data = ProfileStore(self.rt).current(member).data
            score = float(((data.get("personality") or {}).get("五项") or {}).get("外向", 3))
        except Exception:  # noqa: BLE001 — 没有档案：按中间分
            score = 3.0
        return max(0.3, 1 + self.rules.extrovert * (score - 3))

    def sent(self, urge: Urge, msg_id: int, at: datetime) -> None:
        data = self._load()
        data["members"][urge.member] = {"at": iso(at), "msg": msg_id, "reason": urge.reason}
        today = home_zone(self.rt).local(at).date().isoformat()
        data["days"][today] = data["days"].get(today, 0) + 1
        self._save(data)
        if urge.tag:
            from roundtable.core.member.hooks import Hooks

            Hooks(self.rt).after(urge.member, [urge.tag])
