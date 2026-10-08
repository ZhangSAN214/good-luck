"""预算守卫：每月 $20（UTC 每月 1 号重置）、80% 提醒、超出暂停；每日上限（UTC 0 点重置）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from roundtable.core.budget import BudgetGuard, day_window, month_window
from roundtable.core.config.schema import Budget
from roundtable.core.providers import Completion, Message
from roundtable.core.storage import Repository, connect, iso

POLICY = Budget(monthly_usd=20.0, daily_usd=3.0, warn_ratio=0.8)


def dt(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def env():
    clock = Clock(dt(2026, 10, 15, 12))
    repo = Repository(connect(), clock=lambda: iso(clock.now))
    session = repo.create_session("q", seed=1)
    guard = BudgetGuard(POLICY, repo, now=clock)
    return clock, repo, session, guard


def spend(repo: Repository, session: str, usd: float) -> None:
    completion = Completion(
        text="x",
        model_id="m",
        channel="c",
        channel_kind="direct",
        route_model="m",
        input_tokens=1,
        output_tokens=1,
        cached_tokens=0,
        cost_usd=usd,
        cost_source="reported",
        latency_s=0.1,
    )
    repo.record_call(
        session,
        step="answer",
        role="member",
        model_id="m",
        messages=[Message("user", "q")],
        completion=completion,
    )


# --- 时间窗口 -----------------------------------------------------------------


@pytest.mark.parametrize(
    "now, start, end",
    [
        (dt(2026, 10, 15, 12), dt(2026, 10, 1), dt(2026, 11, 1)),
        (dt(2026, 10, 1), dt(2026, 10, 1), dt(2026, 11, 1)),  # 1 号 0 点属于新月份
        (dt(2026, 12, 31, 23, 59), dt(2026, 12, 1), dt(2027, 1, 1)),  # 跨年
        (dt(2028, 2, 29, 8), dt(2028, 2, 1), dt(2028, 3, 1)),
    ],
)
def test_month_window(now, start, end):
    assert month_window(now) == (start, end)


def test_windows_use_utc_not_local_time():
    beijing = timezone(timedelta(hours=8))
    local = datetime(2026, 11, 1, 5, 0, tzinfo=beijing)  # = 2026-10-31 21:00 UTC
    assert month_window(local) == (dt(2026, 10, 1), dt(2026, 11, 1))
    assert day_window(local) == (dt(2026, 10, 31), dt(2026, 11, 1))


def test_day_window():
    assert day_window(dt(2026, 10, 15, 23, 59, 59)) == (dt(2026, 10, 15), dt(2026, 10, 16))


# --- 统计 ---------------------------------------------------------------------


def test_status_counts_only_current_periods(env):
    clock, repo, session, guard = env
    clock.now = dt(2026, 9, 30, 23)
    spend(repo, session, 5.0)  # 上个月
    clock.now = dt(2026, 10, 14, 10)
    spend(repo, session, 1.0)  # 本月、昨天
    clock.now = dt(2026, 10, 15, 9)
    spend(repo, session, 0.5)  # 今天
    clock.now = dt(2026, 10, 15, 12)
    month, day = guard.status()
    assert month.spent_usd == pytest.approx(1.5)
    assert day.spent_usd == pytest.approx(0.5)
    assert month.resets_at == dt(2026, 11, 1) and day.resets_at == dt(2026, 10, 16)
    assert month.remaining_usd == pytest.approx(18.5)


# --- 检查 ---------------------------------------------------------------------


def test_allowed_under_limits(env):
    _, repo, session, guard = env
    spend(repo, session, 1.0)
    v = guard.check(0.5)
    assert v.allowed and v.warnings == () and v.card() is None


def test_monthly_warning_at_80_percent(env):
    clock, repo, session, guard = env
    for d in range(1, 9):  # 分散在不同日期，避开每日上限
        clock.now = dt(2026, 10, d, 10)
        spend(repo, session, 2.0)  # 共 16 = 80%
    clock.now = dt(2026, 10, 15, 12)
    v = guard.check(0.1)
    assert v.allowed
    assert len(v.warnings) == 1 and "本月" in v.warnings[0] and "80%" in v.warnings[0]


def test_daily_warning_at_80_percent(env):
    _, repo, session, guard = env
    spend(repo, session, 2.4)
    v = guard.check(0.1)
    assert v.allowed and any("今日" in w for w in v.warnings)


def test_monthly_exceeded_blocks_until_next_month(env):
    clock, repo, session, guard = env
    for d in range(1, 11):
        clock.now = dt(2026, 10, d, 10)
        spend(repo, session, 2.0)  # 共 20 = 用完
    clock.now = dt(2026, 10, 15, 12)
    v = guard.check(0.0)
    assert not v.allowed and v.blocked_by == ("month",)
    clock.now = dt(2026, 11, 1, 0, 0)  # 11 月 1 日 0 点 UTC：重置
    assert guard.check(0.5).allowed


def test_estimate_that_would_exceed_blocks(env):
    clock, repo, session, guard = env
    for d in range(1, 10):
        clock.now = dt(2026, 10, d, 10)
        spend(repo, session, 2.0)  # 18
    clock.now = dt(2026, 10, 15, 12)
    assert guard.check(1.9).allowed
    v = guard.check(2.5)
    assert v.blocked_by == ("month",)


def test_exactly_reaching_limit_is_allowed(env):
    _, repo, session, guard = env
    spend(repo, session, 2.9)
    assert guard.check(0.1).allowed  # 2.9 + 0.1 正好用满，不算超出
    spend(repo, session, 0.1)
    assert not guard.check(0.0).allowed  # 用满之后再继续才暂停


def test_daily_cap_blocks_until_tomorrow(env):
    clock, repo, session, guard = env
    spend(repo, session, 2.9)
    v = guard.check(0.2)
    assert v.blocked_by == ("day",)
    assert v.month.spent_usd < v.month.limit_usd  # 月度预算还够
    card = v.card()
    assert card.kind == "budget" and "今日" in card.situation
    assert "2026-10-16 00:00 UTC" in card.reason
    clock.now = dt(2026, 10, 16, 0, 1)
    assert guard.check(0.2).allowed


def test_both_limits_block(env):
    clock, repo, session, guard = env
    for d in range(1, 10):
        clock.now = dt(2026, 10, d, 10)
        spend(repo, session, 2.0)
    clock.now = dt(2026, 10, 15, 12)
    spend(repo, session, 1.5)  # 本月 19.5，今日 1.5
    v = guard.check(2.0)
    assert v.blocked_by == ("month", "day")


def test_daily_cap_disabled():
    repo = Repository(connect())
    session = repo.create_session("q", seed=1)
    guard = BudgetGuard(Budget(monthly_usd=20, daily_usd=None, warn_ratio=0.8), repo)
    spend(repo, session, 5.0)
    month, day = guard.status()
    assert day is None
    assert guard.check(1.0).allowed


def test_negative_estimate_rejected(env):
    with pytest.raises(ValueError):
        env[3].check(-1)


def test_budget_card_format(env):
    _, repo, session, guard = env
    spend(repo, session, 3.0)
    card = guard.check(0.4).card()
    assert card.option_keys() == ["continue", "stop"]
    assert card.recommendation == "stop"
    assert "$3.00 / $3.00" in card.situation and "$0.40" in card.situation
    d = card.to_dict()
    assert d["details"]["blocked_by"] == ["day"] and d["details"]["month"]["limit_usd"] == 20.0
