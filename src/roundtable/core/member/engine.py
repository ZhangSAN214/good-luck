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

from roundtable.core.member.about import AboutYou
from roundtable.core.member.brain import Brain
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.context import Context, Piece, assemble
from roundtable.core.member.heartbeat import Heartbeat
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.member.profile import ProfileStore
from roundtable.core.member.relation import RelationPlugin
from roundtable.core.member.scheduler import BudgetScheduler
from roundtable.core.member.state import StatePlugin
from roundtable.core.storage.repository import iso

LAST_KEY = "engine.last_tick"
PRUNED_KEY = "engine.pruned_day"


class MemberEngine:
    def __init__(
        self,
        rt: Any,
        members: Callable[[], list[str]],
        names: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.rt = rt
        self.members = members  # 现在有哪些成员（id）
        self.profiles = ProfileStore(rt)
        self.bus = EventBus(rt)
        self.scheduler = BudgetScheduler(rt)
        self.heartbeat = Heartbeat(rt, self.bus, self.scheduler)
        # P3 状态：订阅总线，按事件算心情和精力（names：成员 id → 称呼，认 @ 用）
        self.state = StatePlugin(rt, self.bus, names or (lambda: {m: m for m in self.members()}))
        # P4 关系：订阅总线，按共同经历累积和你的阶段、同事之间的亲近度
        self.relation = RelationPlugin(rt, self.bus, names)
        # P8 关于你：8 位共用的用户画像（线索由夜间整理交来，每周在这里整合一次）
        self.about = AboutYou(rt)
        # 大脑分区：订阅总线结算驱力（说了话、和人待在一起）；意图卡由聊天和生活说话前现算
        self.brain = Brain(rt, self.bus, members=members)

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

    def observe(self, now: datetime) -> int:
        """只把上次以来的事件发到总线（不心跳、不花钱）。用户发了消息、成员回应前调一次，
        这样被 @、被批评的反射马上算进状态。返回发了几个事件。"""
        if not self.rules.enabled:
            return 0
        repo = self.rt.repo
        end = now + timedelta(milliseconds=1)  # 含 now 这一刻的消息
        last = repo.setting(LAST_KEY)
        start = datetime.fromisoformat(last) if last else now - timedelta(hours=1)
        start = max(start, end - timedelta(days=self.rules.bus.catch_up_days))
        if start >= end:
            return 0
        n = self.bridge(start, end)
        repo.set_setting(LAST_KEY, end.isoformat())
        return n

    async def tick(self, now: datetime) -> dict[str, int]:
        """推进到 now（带时区）。返回 事件 / 醒着 / 意图 / 做了 / 降级 / 清掉 的数量。"""
        out = {"events": 0, "awake": 0, "intents": 0, "done": 0, "noted": 0, "pruned": 0}
        if not self.rules.enabled:
            return out
        repo = self.rt.repo
        last = repo.setting(LAST_KEY)
        start = datetime.fromisoformat(last) if last else now - timedelta(hours=1)
        start = max(start, now - timedelta(days=self.rules.bus.catch_up_days))
        if start < now:
            out["events"] = self.bridge(start, now)
        repo.set_setting(LAST_KEY, now.isoformat())
        for member in self.members():  # 大脑分区：盼头到期见分晓，攒下的奖赏信号发给 P3
            out["signals"] = out.get("signals", 0) + self.brain.flush_signals(member, self.bus, now)
        out |= await self.heartbeat.beat(self.members(), now)
        today = now.date().isoformat()
        if repo.setting(PRUNED_KEY) != today:
            out["pruned"] = self.bus.prune(now)
            repo.set_setting(PRUNED_KEY, today)
        if self.about.weekly(now) is not None:
            out["about_week"] = 1
        return out
