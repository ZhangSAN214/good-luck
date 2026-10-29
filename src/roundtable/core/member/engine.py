"""成员引擎：把自我档案、事件总线、心跳、上下文组装器、预算调度器装在一起（docs/MEMBER_ENGINE.md）。

- 服务启动时建一个（``RoundtableService.engine``），由生活的后台循环每次推进后调 ``tick()``。
- ``tick()``：把上次以来生活引擎记下的事件（``life_events``）和聊天消息转成事件发到总线
  （``life.event`` / ``chat.message``，只读已有的表，不改生活和聊天的代码路径），然后心跳一次，
  每天清一次过期事件。全程不调用模型；以后的模块在总线上订阅、在心跳上登记。
- ``context()``：按 ``chat.engine.context`` 的预算组装一段上下文。
- 只用于聊天、朋友圈、生活；工作模式不读取。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.context import Context, Piece, assemble
from roundtable.core.member.heartbeat import Heartbeat
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.member.profile import ProfileStore
from roundtable.core.member.scheduler import BudgetScheduler
from roundtable.core.storage.repository import iso

LAST_KEY = "engine.last_tick"
PRUNED_KEY = "engine.pruned_day"


class MemberEngine:
    def __init__(self, rt: Any, members: Callable[[], list[str]]) -> None:
        self.rt = rt
        self.members = members  # 现在有哪些成员（id）
        self.profiles = ProfileStore(rt)
        self.bus = EventBus(rt)
        self.scheduler = BudgetScheduler(rt)
        self.heartbeat = Heartbeat(rt, self.bus, self.scheduler)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine

    def context(self, pieces: list[Piece], mode: Mode | str = Mode.LIFE) -> Context:
        """组装上下文；只在生活模式用，工作模式调用会报错（工作与生活分开）。"""
        require_life(mode)
        r = self.rules.context
        return assemble(pieces, r.budget_chars, r.per_source, r.min_piece_chars)

    def bridge(self, start: datetime, end: datetime) -> int:
        """把 [start, end) 内的生活事件和聊天消息发到总线。返回发了几个事件。"""
        repo, n = self.rt.repo, 0
        for e in repo.life_events_between(iso(start), iso(end)):
            data = {k: e[k] for k in ("kind", "place", "members", "room", "written")}
            for member in e["members"]:
                self.bus.publish(Event("life.event", datetime.fromisoformat(e["at"]), member, data))
                n += 1
        n += self.bridge_work(start, end)
        for m in repo.chat_messages_between(iso(start), iso(end)):
            data = {"room": m["room"], "id": m["id"], "life": bool((m["meta"] or {}).get("life"))}
            author = None if m["author"] in ("user", "system") else m["author"]
            data["from_user"] = m["author"] == "user"
            at = datetime.fromisoformat(m["created_at"])
            self.bus.publish(Event("chat.message", at, author, data))
            n += 1
        return n

    def bridge_work(self, start: datetime, end: datetime) -> int:
        """生活模式只读工作：[start, end) 内成员在办公室办过的活 → ``work.done`` 经历事件。

        只读、不反写工作；匿名开启且未揭晓的场次不读。"""
        vendor = {m.id: m.vendor for m in self.rt.config.models.models}
        n = 0
        for row in self.rt.repo.office_work_between(start, end):
            if row["anonymous"] and not row["revealed_at"]:
                continue
            member = vendor.get(row["model_id"])
            if member is None:
                continue
            data = {"session_id": row["session_id"], "question": row["question"][:200]}
            self.bus.publish(Event("work.done", datetime.fromisoformat(row["at"]), member, data))
            n += 1
        return n

    async def tick(self, now: datetime) -> dict[str, int]:
        """推进到 now（带时区）。返回 事件 / 醒着 / 意图 / 做了 / 降级 / 清掉 的数量。"""
        out = {"events": 0, "awake": 0, "intents": 0, "done": 0, "noted": 0, "pruned": 0}
        if not self.rules.enabled:
            return out
        repo = self.rt.repo
        last = repo.setting(LAST_KEY)
        start = datetime.fromisoformat(last) if last else now - timedelta(hours=1)
        start = max(start, now - timedelta(days=self.rules.bus.keep_days))
        if start < now:
            out["events"] = self.bridge(start, now)
        repo.set_setting(LAST_KEY, now.isoformat())
        out |= await self.heartbeat.beat(self.members(), now)
        today = now.date().isoformat()
        if repo.setting(PRUNED_KEY) != today:
            out["pruned"] = self.bus.prune(now)
            repo.set_setting(PRUNED_KEY, today)
        return out
