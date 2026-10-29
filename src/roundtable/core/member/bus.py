"""事件总线：聊天、生活、感知产生的事都发成事件，模块按需订阅，用程序更新自己（不调用模型）。

- 事件：``kind``（如 ``life.event``、``chat.user``、``heartbeat``）、相关成员、时间、数据。
- 订阅：``subscribe(kind, handler)``，``kind`` 写 ``*`` 收全部；也可以写前缀 ``life.*``。
  处理函数同步执行；某个处理函数出错只记日志，不影响别的订阅者和发事件的一方。
- 事件存进 ``member_events``（迁移 24，只存本地），保留 ``chat.engine.bus.keep_days`` 天，
  之后的模块（记忆、情绪、关系……）开机时可以从这里补算。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.storage.repository import iso

log = logging.getLogger(__name__)

Handler = Callable[["Event"], None]


@dataclass(frozen=True)
class Event:
    kind: str
    at: datetime
    member: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


PERCEIVE = "perceive."  # 感知事件的前缀：perceive.video / perceive.weather / perceive.map …


def perception(what: str, at: datetime, member: str, card: str, source: str = "") -> Event:
    """感知事件（预留，docs/MEMBER_ENGINE.md「扩展：感知事件」）：感知者 = member，
    card 是感知卡文本（看过 / 听过 / 读过的结果），source 是出处（网址、文件名等）。"""
    return Event(f"{PERCEIVE}{what}", at, member, {"card": card, "source": source})


def matches(pattern: str, kind: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return kind.startswith(pattern[:-1])
    return pattern == kind


class EventBus:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self._subs: list[tuple[str, Handler]] = []

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.bus

    def subscribe(self, kind: str, handler: Handler) -> Callable[[], None]:
        """订阅；返回取消订阅的函数。"""
        entry = (kind, handler)
        self._subs.append(entry)
        return lambda: self._subs.remove(entry) if entry in self._subs else None

    def publish(self, event: Event) -> int:
        """发一个事件：存库，再依次交给订阅者。返回成功处理的订阅者数。"""
        if self.rules.persist:
            self.rt.repo.add_member_event(iso(event.at), event.kind, event.member, event.data)
        done = 0
        for pattern, handler in list(self._subs):
            if not matches(pattern, event.kind):
                continue
            try:
                handler(event)
                done += 1
            except Exception:  # noqa: BLE001 - 一个模块出错不影响别的模块
                log.exception("事件总线：处理 %s 出错", event.kind)
        return done

    def replay(self, start: datetime, end: datetime, kind: str = "*") -> list[Event]:
        """[start, end) 内存下的事件（按时间先后），开机补算用。"""
        rows = self.rt.repo.member_events_between(iso(start), iso(end))
        out = [
            Event(r["kind"], datetime.fromisoformat(r["at"]), r["member"], r["data"]) for r in rows
        ]
        return [e for e in out if matches(kind, e.kind)]

    def prune(self, now: datetime) -> int:
        """删掉 keep_days 天以前的事件。返回删掉的条数。"""
        return self.rt.repo.prune_member_events(iso(now - timedelta(days=self.rules.keep_days)))
