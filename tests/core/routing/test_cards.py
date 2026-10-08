from __future__ import annotations

from roundtable.core.routing import (
    OutcomeSignals,
    Question,
    UserChoice,
    cost_card,
    escalation_card,
    plan_escalation,
    route_question,
)

from .conftest import planner_reply

Q = Question("求函数 f(x)=x^3-3x 在区间 [-2, 2] 上的最大值与最小值，并写出完整过程。")


async def decide(env, choice=None):
    return await route_question(
        Q, choice, config=env.config, router=env.router, prompts=env.prompts, seed=2
    )


async def test_cost_card_offers_alternatives(env):
    d = await decide(env, UserChoice("preset", preset="strongest"))
    card = cost_card(d)
    assert card.kind == "cost" and card.recommendation == "continue"
    assert card.option_keys()[0] == "continue" and card.option_keys()[-1] == "stop"
    assert {"plan:simple", "plan:medium"} <= set(card.option_keys())
    assert card.options[0].cost_usd == d.estimate.total_usd
    assert "旗舰圆桌" in card.situation


async def test_cost_card_for_manual_choice(env):
    d = await decide(env, UserChoice("manual", members=("f1", "f2")))
    card = cost_card(d)
    assert "手动选择" in card.options[0].label


async def test_escalation_card(env):
    env.fake.queue("b1", planner_reply("medium"))
    d = await decide(env)
    up = plan_escalation(d, Q, OutcomeSignals(2, "low"), config=env.config, router=env.router)
    card = escalation_card(up)
    assert card.option_keys() == ["continue", "accept", "stop"]
    from roundtable.core.cards import money

    assert "分歧" in card.situation and money(up.estimate.total_usd) in card.situation
