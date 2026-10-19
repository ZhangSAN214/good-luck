"""名册：每位能上桌的模型是一名员工（或经理），显示工资标准与本月已领工资。

工资标准就是 models.yaml 里的 token 价格（美元 / 每百万 token）；已领工资是本月（UTC 自然月）
调用记录里该模型的花费合计。名册只看配置和调用记录，不涉及任何具体会话，不影响匿名。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from roundtable.core.allocation.names import display_name
from roundtable.core.config import AppConfig


@dataclass(frozen=True)
class RosterEntry:
    model_id: str
    name: str  # 昵称·模式
    role: str  # manager / staff
    tier: str | None
    tags: tuple[str, ...]
    wage_input: float  # 美元 / 每百万输入 token
    wage_output: float  # 美元 / 每百万输出 token
    available: bool
    month_calls: int
    month_input_tokens: int
    month_output_tokens: int
    month_paid_usd: float


ROLE_ORDER = {"manager": 0, "staff": 1}


def roster(
    config: AppConfig,
    available: Iterable[str],
    month_usage: Mapping[str, Mapping[str, Any]],
) -> list[RosterEntry]:
    """经理在前，其余按工资标准（输出价、输入价）从高到低；工资相同时保持配置顺序。"""
    online = set(available)
    staff = [m for m in config.models.enabled if m.seat and m.tier is not None]
    entries = []
    for m in staff:
        used = month_usage.get(m.id, {})
        entries.append(
            RosterEntry(
                model_id=m.id,
                name=display_name(m.id, config),
                role=m.office_role,
                tier=m.tier,
                tags=tuple(m.tags),
                wage_input=m.price.input,
                wage_output=m.price.output,
                available=m.id in online,
                month_calls=int(used.get("calls", 0)),
                month_input_tokens=int(used.get("input_tokens", 0)),
                month_output_tokens=int(used.get("output_tokens", 0)),
                month_paid_usd=float(used.get("cost_usd", 0.0)),
            )
        )
    return sorted(entries, key=lambda e: (ROLE_ORDER.get(e.role, 9), -e.wage_output, -e.wage_input))
