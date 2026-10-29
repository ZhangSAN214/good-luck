"""渠道优先顺序：同一模型有多个渠道时先走哪个（只决定走哪条路，不影响谁上桌）。

- auto：按模型 routes 的配置顺序。
- custom：按用户列出的渠道先后，没列出的保持配置顺序排在后面。
- 两种方式下都看每月额度：填了额度的渠道本月还有余额 → 排最前；用完了 → 排最后。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from roundtable.core.config import Route

Strategy = Literal["auto", "custom"]
STRATEGIES: tuple[Strategy, ...] = ("auto", "custom")


@dataclass(frozen=True)
class ChannelPriority:
    strategy: Strategy = "auto"
    order: tuple[str, ...] = ()
    # 渠道 → 每月额度（美元）；没有的渠道不按额度排
    credits: Mapping[str, float] = field(default_factory=dict)


def credit_state(credit: float | None, spent: float) -> str:
    """none：没设额度；left：本月还有；used：本月用完。"""
    if credit is None:
        return "none"
    return "left" if spent < credit else "used"


def order_routes(
    routes: Sequence[Route],
    priority: ChannelPriority,
    spent: Callable[[str], float],
) -> list[Route]:
    """按优先规则排好的路由（稳定排序：同一档内保持配置顺序）。spent(渠道) = 本月已用。"""
    rank = {name: i for i, name in enumerate(priority.order)}
    tail = len(rank)
    band = {"left": 0, "none": 1, "used": 2}

    def key(indexed: tuple[int, Route]) -> tuple[int, int, int]:
        i, route = indexed
        credit = priority.credits.get(route.channel)
        state = credit_state(credit, spent(route.channel) if credit is not None else 0.0)
        custom = rank.get(route.channel, tail) if priority.strategy == "custom" else 0
        return band[state], custom, i

    return [r for _, r in sorted(enumerate(routes), key=key)]
