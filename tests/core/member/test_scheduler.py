"""预算调度器：用户直接找她 > 主动找你 > 群聊 > 生活；到额度降级只记一行；从不超支。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from roundtable.core.member.scheduler import BudgetScheduler, Priority, Request

from ..orchestrator.conftest import Env

NOW = datetime(2026, 10, 29, 10, tzinfo=UTC)


def make(spent: float = 0.0, monthly_left: float = 5.0) -> BudgetScheduler:
    env = Env()
    env.rt.budget.now = lambda: NOW
    s = BudgetScheduler(env.rt)
    s.spent_today = lambda: spent
    s._monthly_left = lambda: monthly_left
    return s


def test_shares_shrink_with_priority():
    s = make()
    limits = [s.limit_for(p) for p in (Priority.PROACTIVE, Priority.GROUP, Priority.LIFE)]
    assert s.limit_for(Priority.USER) is None
    assert limits == sorted(limits, reverse=True) and limits[-1] > 0


def test_lower_priorities_degrade_first():
    life_limit = make().limit_for(Priority.LIFE)
    s = make(spent=life_limit)
    assert s.decide(Priority.LIFE, 0.01).action == "note"
    assert s.decide(Priority.GROUP, 0.01).call
    assert s.decide(Priority.USER, 0.01).call
    # 用户直接找她从不因每日额度降级
    s = make(spent=100.0)
    assert s.decide(Priority.USER, 0.01).call
    assert s.decide(Priority.PROACTIVE, 0.01).action == "note"


def test_month_exhausted_blocks_user_and_notes_others():
    s = make(monthly_left=0.001)
    assert s.decide(Priority.USER, 0.01).action == "blocked"
    assert s.decide(Priority.LIFE, 0.01).action == "note"


def test_plan_orders_by_priority_and_never_overspends():
    s = make()
    group_limit = s.limit_for(Priority.GROUP)
    est = group_limit / 2 + 1e-6  # 群聊额度只够一件
    reqs = [
        Request(Priority.LIFE, 0.0001, "生活"),
        Request(Priority.GROUP, est, "群聊1"),
        Request(Priority.GROUP, est, "群聊2"),
        Request(Priority.USER, est, "找她"),
    ]
    plan = s.plan(reqs)
    assert [r.label for r, _ in plan] == ["找她", "群聊1", "群聊2", "生活"]
    acts = {r.label: d.action for r, d in plan}
    assert acts["找她"] == "call"
    assert acts["群聊1"] == "note" and acts["群聊2"] == "note"  # 找她已占用额度
    assert acts["生活"] == "call"  # 生活额度里还放得下这件小事
    total = sum(r.estimate_usd for r, d in plan if d.call)
    assert total <= s.limit_for(Priority.LIFE)


def test_negative_estimate_rejected():
    with pytest.raises(ValueError):
        make().decide(Priority.LIFE, -1)


def paced(before: float, *, pace: bool = True, monthly: float = 5.0) -> BudgetScheduler:
    from ..orchestrator.conftest import Env as _Env

    chat = _Env().config.roundtable.chat
    budget = chat.budget.model_copy(update={"pace": pace, "monthly_usd": monthly})
    env = _Env(rt_update={"chat": chat.model_copy(update={"budget": budget})})
    env.rt.budget.now = lambda: NOW  # 10 月 29 日：含今天还剩 3 天
    env.rt.repo.spent_in_workflow = lambda wf, start, end: before
    s = BudgetScheduler(env.rt)
    s.spent_today = lambda: 0.0
    s._monthly_left = lambda: monthly - before
    return s


def test_pacing_spreads_what_is_left_of_the_month():
    s = paced(before=4.7)  # 还剩 $0.30 / 3 天 = $0.10 一天，小于每日额度 $0.25
    assert s.daily_budget() == pytest.approx(0.10)
    assert s.limit_for(Priority.LIFE) == pytest.approx(0.10 * s.rules.shares.life)
    assert s.decide(Priority.PROACTIVE, 0.09).action == "note"
    assert s.decide(Priority.USER, 0.09).call  # 你直接找她不受平摊限制
    # 月初钱多：照旧按配置的每日额度
    assert paced(before=0.0).daily_budget() == pytest.approx(s.rules.daily_usd)


def test_pacing_can_be_turned_off():
    s = paced(before=4.7, pace=False)
    assert s.paced_daily() is None
    assert s.daily_budget() == pytest.approx(s.rules.daily_usd)
