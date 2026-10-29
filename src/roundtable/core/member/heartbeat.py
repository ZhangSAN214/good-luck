"""心跳调度：每隔一段时间（由代码）看谁醒着、有没有值得做的事；没事就什么都不调用。

- 模块用 ``register(name, want)`` 登记「想做的事」：``want(member, now)`` 由代码判断，返回
  ``Intent``（优先级、预估花费、要做的事 ``run``、降级时记的一行 ``note``）或 None。
- 每次心跳：醒着的成员（按公寓楼作息，``sleep`` 的不算）× 登记的模块，收集意图，交给预算调度器
  排队；能做的依次执行（每次心跳最多 ``max_actions`` 件），其余降级为只记一行（发 ``engine.note``
  事件）。每次心跳发一个 ``heartbeat`` 事件（醒着几位、几件意图、做了几件）。
- 现在还没有模块登记（骨架），所以心跳不花钱。之后的行为决策（M7）等模块在这里登记。
- 所有成员的规则完全相同：每位成员问同样的模块，模块不看是哪个模型。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.scheduler import BudgetScheduler, Priority, Request
from roundtable.core.office.apartment import schedule

log = logging.getLogger(__name__)


@dataclass
class Intent:
    member: str
    priority: Priority
    estimate_usd: float
    run: Callable[[], Awaitable[Any]]
    note: str  # 降级时只记这一行
    source: str = ""  # 哪个模块


Want = Callable[[str, datetime], "Intent | None"]


class Heartbeat:
    def __init__(self, rt: Any, bus: EventBus, scheduler: BudgetScheduler) -> None:
        self.rt = rt
        self.bus = bus
        self.scheduler = scheduler
        self._wants: dict[str, Want] = {}

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.heartbeat

    def register(self, name: str, want: Want) -> None:
        self._wants[name] = want

    def unregister(self, name: str) -> None:
        self._wants.pop(name, None)

    @staticmethod
    def awake(members: list[str], now: datetime) -> list[str]:
        return [m for m in members if schedule(m, now.hour, now.weekday())[1] != "sleep"]

    def intents(self, members: list[str], now: datetime) -> list[Intent]:
        out: list[Intent] = []
        for member in self.awake(members, now):
            for name, want in sorted(self._wants.items()):
                try:
                    intent = want(member, now)
                except Exception:  # noqa: BLE001 - 一个模块出错不影响别的模块
                    log.exception("心跳：模块 %s 出错", name)
                    continue
                if intent is not None:
                    intent.source = intent.source or name
                    out.append(intent)
        return out

    async def beat(self, members: list[str], now: datetime) -> dict[str, int]:
        """一次心跳。now 带时区（本地时间决定作息）。返回 醒着 / 意图 / 做了 / 降级 的数量。"""
        out = {"awake": 0, "intents": 0, "done": 0, "noted": 0}
        if not self.rules.enabled:
            return out
        awake = self.awake(members, now)
        intents = self.intents(awake, now)
        out["awake"], out["intents"] = len(awake), len(intents)
        plan = self.scheduler.plan([Request(i.priority, i.estimate_usd, i.source) for i in intents])
        order = sorted(range(len(intents)), key=lambda k: (intents[k].priority, k))
        for k, (_, decision) in zip(order, plan, strict=True):
            intent = intents[k]
            if decision.call and out["done"] < self.rules.max_actions:
                try:
                    await intent.run()
                    out["done"] += 1
                    continue
                except Exception:  # noqa: BLE001 - 做事出错就只记一行
                    log.exception("心跳：%s 的 %s 出错", intent.member, intent.source)
            out["noted"] += 1
            self.bus.publish(
                Event(
                    "engine.note",
                    now,
                    intent.member,
                    {"source": intent.source, "note": intent.note, "reason": decision.reason},
                )
            )
        self.bus.publish(Event("heartbeat", now, None, dict(out)))
        return out
