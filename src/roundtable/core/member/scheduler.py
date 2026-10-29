"""预算调度器：每天的上限内按优先级排队，重要的先做，永远不超支。

优先级（从高到低）：用户直接找她 > 主动找你 > 群聊 > 生活。

- 用户直接找她：只受聊天月预算和总预算限制，**从不因为每日额度被降级**。
- 其余三类：今天聊天这一块（``workflow = 'chat'``）已花的钱加上这次的预估，超过
  ``daily_usd × 该优先级的份额``（``shares``，越低的份额越小，给高优先级留出余地）时降级为
  「只记一行」（``note``），不调用模型。生活另外还受生活每日上限（``chat.life.daily_usd``）限制。
- 一批请求用 ``plan()`` 先按优先级、再按交上来的顺序排好，依次占用额度。
- 所有成员的额度和规则完全相同（中立）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from roundtable.core.budget.guard import EPS, day_window, month_window


class Priority(IntEnum):
    USER = 0  # 用户直接找她
    PROACTIVE = 1  # 主动找你
    GROUP = 2  # 群聊
    LIFE = 3  # 生活


SHARE_KEYS = {Priority.PROACTIVE: "proactive", Priority.GROUP: "group", Priority.LIFE: "life"}


@dataclass(frozen=True)
class Decision:
    action: str  # call 调用模型 / note 只记一行 / blocked 没钱了（用户直接找她时）
    reason: str = ""

    @property
    def call(self) -> bool:
        return self.action == "call"


@dataclass(frozen=True)
class Request:
    priority: Priority
    estimate_usd: float
    label: str = ""


class BudgetScheduler:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.scheduler

    def spent_today(self) -> float:
        start, end = day_window(self.rt.budget.now())
        return self.rt.repo.spent_in_workflow("chat", start, end)

    def _monthly_left(self) -> float:
        start, end = month_window(self.rt.budget.now())
        limit = self.rt.config.roundtable.chat.budget.monthly_usd
        return limit - self.rt.repo.spent_in_workflow("chat", start, end)

    def limit_for(self, priority: Priority) -> float | None:
        """这个优先级今天最多能用到的聊天花费；用户直接找她时为 None（不设每日额度）。"""
        if priority == Priority.USER:
            return None
        return self.rules.daily_usd * getattr(self.rules.shares, SHARE_KEYS[priority])

    def decide(self, priority: Priority, estimate_usd: float, *, pending: float = 0.0) -> Decision:
        """pending：同一批里排在前面、已经占用的预估花费。"""
        if estimate_usd < 0:
            raise ValueError("预计花费不能为负")
        need = pending + estimate_usd
        if not self.rt.budget.check(need).allowed or self._monthly_left() + EPS < need:
            if priority == Priority.USER:
                return Decision("blocked", "聊天月预算或总预算用完了")
            return Decision("note", "聊天月预算或总预算不够")
        limit = self.limit_for(priority)
        if limit is not None and self.spent_today() + need > limit + EPS:
            return Decision("note", f"今天这类的额度（${limit:.3f}）用完了，只记一行")
        return Decision("call")

    def plan(self, requests: list[Request]) -> list[tuple[Request, Decision]]:
        """一批请求按优先级排队，依次占用额度。返回 (请求, 决定)，顺序为执行顺序。"""
        order = sorted(enumerate(requests), key=lambda x: (x[1].priority, x[0]))
        out: list[tuple[Request, Decision]] = []
        pending = 0.0
        for _, req in order:
            decision = self.decide(req.priority, req.estimate_usd, pending=pending)
            if decision.call:
                pending += req.estimate_usd
            out.append((req, decision))
        return out
