"""发育期可塑性（成长 G2，2026-10-28 采纳的「发育式成长」）：新来的成员变得快，待久了变得慢。

- **入职多久**：她的自我档案第一个版本（人物卡导入或第一次建档）到今天的天数。
- **发育期**（前 ``young_days`` 天）：照原来的规则，每周最多 ``growth.max_changes`` 条、
  性格分每月一格。
- **定型以后**：每周最多 ``settled_changes`` 条；性格分要和上一次改性格隔够 ``settle_days`` 天，
  这个间隔按性格微调——好奇、外向的人变得快一点，较真的人慢一点（``trait_speed``，
  每比 3 分高一分，间隔乘上 1 + 系数）。
- 全程序、不调模型；8 位同一套规则，差别只来自入职时间和性格分。只用于生活模式。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from roundtable.core.member.growth.gate import SCORE
from roundtable.core.member.growth.timeline import CHANGE, GrowthLog


@dataclass(frozen=True)
class Plasticity:
    tenure_days: int  # 入职多少天
    young: bool  # 还在发育期
    max_changes: int  # 这周最多改几条
    core_gap_days: int  # 性格分两次改动至少隔几天（发育期为 0：只看每月一格）
    core_wait: int  # 离下一次能改性格分还有几天（0 = 现在就能改）

    def line(self) -> str:
        """给成长页看的一句话。"""
        if self.young:
            return f"入职第 {self.tenure_days} 天，还在发育期：变得快"
        wait = f"，还要等 {self.core_wait} 天" if self.core_wait else ""
        return (
            f"入职第 {self.tenure_days} 天，性格已经定型：每周最多改 {self.max_changes} 条，"
            f"性格分隔 {self.core_gap_days} 天才动一格{wait}"
        )


def _first_day(rt: Any, member: str) -> date | None:
    hist = rt.repo.member_profile_history(member)  # 新的在前
    if not hist:
        return None
    return datetime.fromisoformat(hist[-1]["created_at"]).date()


def _traits(data: dict[str, Any]) -> dict[str, float]:
    out = {}
    for k, v in ((data.get("personality") or {}).get("五项") or {}).items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def plasticity(rt: Any, member: str, today: date, data: dict[str, Any]) -> Plasticity:
    """这位成员今天的可塑性。data：她现在的档案数据（看性格分）。"""
    growth = rt.config.roundtable.chat.engine.growth
    r = growth.plastic
    first = _first_day(rt, member) or today
    tenure = max(0, (today - first).days)
    if not r.enabled or tenure < r.young_days:
        return Plasticity(tenure, True, growth.max_changes, 0, 0)
    traits = _traits(data)
    factor = 1.0 + sum(c * (traits.get(t, 3.0) - 3.0) for t, c in r.trait_speed.items())
    gap = max(r.min_gap_days, math.ceil(r.settle_days * max(0.2, factor)))
    last = None
    for e in GrowthLog(rt).load(member)["timeline"]:
        if e.get("kind") == CHANGE and e.get("op") == SCORE and not e.get("reverted"):
            last = datetime.fromisoformat(e["at"]).date()
    wait = 0 if last is None else max(0, gap - (today - last).days)
    return Plasticity(tenure, False, min(growth.max_changes, r.settled_changes), gap, wait)
