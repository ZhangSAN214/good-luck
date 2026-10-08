"""预算守卫：每月预算 + 每日上限，按 UTC 自然月 / 自然日统计。

花费来自数据库中的调用记录，所以"每月 1 号重置""每天 0 点重置"不需要定时任务：
到了新周期，统计窗口自然换成新的月份 / 日期。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from roundtable.core.cards import CardOption, ConfirmationCard, money
from roundtable.core.config.schema import Budget
from roundtable.core.storage import Repository

PeriodName = Literal["month", "day"]
# 浮点误差容差（远小于 1 美分）：避免 2.4/3.0 算成 0.7999…、2.9+0.1 算成超过 3.0
EPS = 1e-9
LABELS = {"month": "本月", "day": "今日"}


def month_window(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


def day_window(now: datetime) -> tuple[datetime, datetime]:
    now = now.astimezone(UTC)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


@dataclass(frozen=True)
class BudgetStatus:
    period: PeriodName
    limit_usd: float
    spent_usd: float
    starts_at: datetime
    resets_at: datetime
    warn_ratio: float

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    @property
    def ratio(self) -> float:
        return self.spent_usd / self.limit_usd

    @property
    def warn(self) -> bool:
        return self.ratio >= self.warn_ratio - EPS

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.limit_usd - EPS

    def would_exceed(self, estimate_usd: float) -> bool:
        return self.exhausted or self.spent_usd + estimate_usd > self.limit_usd + EPS

    def describe(self) -> str:
        return (
            f"{LABELS[self.period]}已用 ${self.spent_usd:.2f} / ${self.limit_usd:.2f}"
            f"（{self.ratio:.0%}），{self.resets_at:%Y-%m-%d %H:%M} UTC 重置"
        )


@dataclass(frozen=True)
class BudgetVerdict:
    estimate_usd: float
    month: BudgetStatus
    day: BudgetStatus | None
    blocked_by: tuple[PeriodName, ...]
    warnings: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return not self.blocked_by

    def card(self) -> ConfirmationCard | None:
        """被拦下时给用户的确认卡片；未被拦下时返回 None。"""
        if self.allowed:
            return None
        statuses = [s for s in (self.month, self.day) if s and s.period in self.blocked_by]
        lines = [s.describe() for s in statuses]
        lines.append(f"下一步预计 {money(self.estimate_usd)}，会超出上述额度，已暂停。")
        if self.blocked_by == ("day",):
            reason = f"只超出每日上限，{self.day.resets_at:%Y-%m-%d %H:%M} UTC 后可在额度内继续"
        else:
            reason = "超出预算的花费不会自动执行；确需继续请明确选择"
        return ConfirmationCard(
            kind="budget",
            situation="\n".join(lines),
            options=(
                CardOption("continue", "超出预算继续", self.estimate_usd, "本次不受预算限制"),
                CardOption("stop", "停止", 0.0, "保留已完成的部分"),
            ),
            recommendation="stop",
            reason=reason,
            details={
                "blocked_by": list(self.blocked_by),
                "month": _status_dict(self.month),
                "day": _status_dict(self.day) if self.day else None,
            },
        )


def _status_dict(s: BudgetStatus) -> dict[str, object]:
    return {
        "limit_usd": s.limit_usd,
        "spent_usd": s.spent_usd,
        "ratio": s.ratio,
        "resets_at": s.resets_at.isoformat(),
    }


class BudgetGuard:
    def __init__(
        self,
        policy: Budget,
        repo: Repository,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.policy = policy
        self.repo = repo
        self.now = now

    def _status(self, period: PeriodName, limit: float, moment: datetime) -> BudgetStatus:
        start, end = month_window(moment) if period == "month" else day_window(moment)
        return BudgetStatus(
            period=period,
            limit_usd=limit,
            spent_usd=self.repo.spent_between(start, end),
            starts_at=start,
            resets_at=end,
            warn_ratio=self.policy.warn_ratio,
        )

    def status(self) -> tuple[BudgetStatus, BudgetStatus | None]:
        moment = self.now()
        month = self._status("month", self.policy.monthly_usd, moment)
        day = self._status("day", self.policy.daily_usd, moment) if self.policy.daily_usd else None
        return month, day

    def check(self, estimate_usd: float = 0.0) -> BudgetVerdict:
        """执行下一步之前调用。estimate_usd 为下一步的预计花费。"""
        if estimate_usd < 0:
            raise ValueError("预计花费不能为负")
        month, day = self.status()
        blocked: list[PeriodName] = []
        warnings: list[str] = []
        for s in (month, day):
            if s is None:
                continue
            if s.would_exceed(estimate_usd):
                blocked.append(s.period)
            elif s.warn:
                warnings.append(f"{s.describe()}，已达到 {s.warn_ratio:.0%} 提醒线")
        return BudgetVerdict(estimate_usd, month, day, tuple(blocked), tuple(warnings))
