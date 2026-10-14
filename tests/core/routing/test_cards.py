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


async def test_cost_card_offers_other_tiers(env):
    d = await decide(env, UserChoice("flagship"))
    card = cost_card(d)
    assert card.kind == "cost" and card.recommendation == "continue"
    assert card.option_keys() == ["continue", "plan:budget", "stop"]
    assert card.options[0].cost_usd == d.estimate.total_usd
    assert "全力模式" in card.situation and "4 位组员 + 1 位统筹" in card.situation


async def test_cost_card_for_custom_choice(env):
    d = await decide(env, UserChoice("custom", ("f1", "f2", "f3")))
    card = cost_card(d)
    assert "自选" in card.options[0].label
    assert {"plan:budget", "plan:flagship"} <= set(card.option_keys())


async def test_cost_card_mentions_absent_count(env):
    from roundtable.core.routing import option_lineup

    available = [m for m in env.router.available_models() if m.id != "f5"]
    env.router.available_models = lambda: available  # f5 没有可用渠道
    lineup = option_lineup("flagship", seed=1, config=env.config, router=env.router)
    assert lineup.absent == ("f5",)
    d = await decide(env, UserChoice("flagship"))
    assert "1 个模型因没有可用渠道缺席" in cost_card(d).situation
    assert "f5" not in cost_card(d).situation  # 只给人数，不给名字（匿名时也安全）


async def test_escalation_card(env):
    env.fake.queue("b1", planner_reply("medium"))
    d = await decide(env)
    up = plan_escalation(d, Q, OutcomeSignals(2, "low"), config=env.config, router=env.router)
    card = escalation_card(up)
    assert card.option_keys() == ["continue", "accept", "stop"]
    from roundtable.core.cards import money

    assert "分歧" in card.situation and money(up.estimate.total_usd) in card.situation
