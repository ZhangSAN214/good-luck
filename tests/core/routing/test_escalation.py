from __future__ import annotations

import pytest

from roundtable.core.config.schema import EscalationRule
from roundtable.core.routing import (
    OutcomeSignals,
    Question,
    UserChoice,
    escalation_reason,
    plan_escalation,
    route_question,
)

from .conftest import planner_reply

Q = Question("求函数 f(x)=x^3-3x 在区间 [-2, 2] 上的最大值与最小值，并写出完整过程。")


@pytest.mark.parametrize(
    "signals, escalate",
    [
        (OutcomeSignals(0, "high"), False),
        (OutcomeSignals(0, "medium"), False),
        (OutcomeSignals(1, "high"), True),
        (OutcomeSignals(0, "low"), True),
        (OutcomeSignals(0, None), False),
    ],
)
def test_escalation_reason(signals, escalate):
    assert (escalation_reason(signals, EscalationRule()) is not None) is escalate


def test_escalation_rule_configurable():
    rule = EscalationRule(min_disagreements=2, confidence=[])
    assert escalation_reason(OutcomeSignals(1, "low"), rule) is None
    assert "2" in escalation_reason(OutcomeSignals(2, "high"), rule)


async def budget_decision(env, choice=None):
    env.fake.queue("b1", planner_reply("medium"))
    return await route_question(
        Q, choice, config=env.config, router=env.router, prompts=env.prompts, seed=5
    )


async def test_escalates_to_flagship_tier(env):
    d = await budget_decision(env)
    up = plan_escalation(d, Q, OutcomeSignals(2, "medium"), config=env.config, router=env.router)
    assert up is not None
    assert (up.plan, up.escalated_from, up.escalate_to) == ("flagship", "budget", None)
    by = {m.id: m for m in env.config.models.models}
    seated = {*up.lineup.members, up.lineup.coordinator}
    assert seated == {m.id for m in env.config.models.enabled if m.tier == "flagship"}
    assert {by[m].tier for m in seated} == {"flagship"}
    assert up.estimate.total_usd > d.estimate.total_usd
    assert "分歧" in up.escalation_reason

    record = d.record(Q).with_outcome(actual_cost_usd=0.3, escalation=up, user_confirmed=True)
    assert record.escalated and record.escalated_plan == "flagship"
    assert record.escalation_estimated_cost_usd == up.estimate.total_usd


async def test_no_escalation_when_consensus(env):
    d = await budget_decision(env)
    assert (
        plan_escalation(d, Q, OutcomeSignals(0, "high"), config=env.config, router=env.router)
        is None
    )


async def test_flagship_never_escalates(env):
    d = await budget_decision(env, UserChoice("flagship"))
    assert (
        plan_escalation(d, Q, OutcomeSignals(5, "low"), config=env.config, router=env.router)
        is None
    )


async def test_custom_never_escalates(env):
    d = await budget_decision(env, UserChoice("custom", ("b1", "b2", "f1")))
    assert (
        plan_escalation(d, Q, OutcomeSignals(5, "low"), config=env.config, router=env.router)
        is None
    )
