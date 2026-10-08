"""路由入口：三种用户模式、确认门槛、方案花费、记录。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.providers import ErrorKind, FakeProvider
from roundtable.core.routing import Question, RoutingError, UserChoice, route_question

from .conftest import Env, app_config, planner_reply

MID_QUESTION = "求函数 f(x)=x^3-3x 在区间 [-2, 2] 上的最大值与最小值，并写出完整过程。"


async def decide(env: Env, text=MID_QUESTION, choice=None, seed=1, **kw):
    return await route_question(
        Question(text, **kw),
        choice,
        config=env.config,
        router=env.router,
        prompts=env.prompts,
        seed=seed,
    )


def tiers(env, ids):
    by = {m.id: m for m in env.config.models.models}
    return [by[i].tier for i in ids]


# --- 自动模式 -------------------------------------------------------------------


async def test_auto_rule_decides_without_calling_model(env):
    d = await decide(env, "1+1=?")
    assert d.assessment.source == "rule" and d.assessment.difficulty == "simple"
    assert env.fake.calls == []  # 规则判断出来了，不调用规划员
    assert d.plan == "simple" and len(d.lineup.members) == 1 and d.lineup.coordinator is None
    assert d.planner_cost_usd == 0


@pytest.mark.parametrize(
    "difficulty, plan, member_tier",
    [("simple", "simple", "budget"), ("medium", "medium", "budget"), ("hard", "hard", "flagship")],
)
async def test_auto_planner_difficulty_to_plan(env, difficulty, plan, member_tier):
    env.fake.queue("b1", planner_reply(difficulty))
    d = await decide(env)
    assert d.assessment.source == "model" and d.assessment.difficulty == difficulty
    assert d.assessment.planner.model_id == "b1"  # 最便宜的便宜档
    assert d.plan == plan
    assert set(tiers(env, d.lineup.members)) == {member_tier}
    assert d.planner_cost_usd > 0


async def test_auto_medium_can_escalate(env):
    env.fake.queue("b1", planner_reply("medium"))
    assert (await decide(env)).escalate_to == "hard"


async def test_auto_planner_failure_uses_default(env):
    env.fake.queue("b1", ErrorKind.QUOTA)
    d = await decide(env)
    assert d.assessment.source == "default"
    assert d.assessment.difficulty == env.config.routing.default_difficulty
    assert "规划员不可用" in d.assessment.reason


async def test_auto_image_requires_vision(env):
    d = await decide(env, "这张图里的函数是什么？", attachments=("image",))
    assert d.assessment.require_tags == ("vision",)
    by = {m.id: m for m in env.config.models.models}
    assert all("vision" in by[m].tags for m in d.lineup.members)


async def test_auto_no_vision_models_is_clear_error():
    pool = [
        p
        for p in __import__("tests.core.routing.conftest", fromlist=["POOL"]).POOL
        if "vision" not in p[3]
    ]
    env = Env(app_config(pool=pool), FakeProvider("c"))
    with pytest.raises(RoutingError, match="vision"):
        await decide(env, "这张图是什么？", attachments=("image",))


async def test_planner_answer_length_used_and_clamped(env):
    env.fake.queue("b1", planner_reply("medium", tokens=10**6))
    d = await decide(env)
    answer = next(s for s in d.estimate.steps if s.step == "answer")
    bounds = env.config.routing.estimate.answer_tokens_bounds
    assert answer.output_tokens == bounds.max * len(d.lineup.members)


# --- 预设与手动：用户选择优先 ---------------------------------------------------


async def test_preset_overrides_difficulty_and_skips_planner(env):
    d = await decide(env, "1+1=?", UserChoice("preset", preset="strongest"))
    assert d.assessment.difficulty == "simple"  # 规则照常记录
    assert d.plan == "hard"  # 但用户选了"最强"
    assert set(tiers(env, d.lineup.members)) == {"flagship"}
    d2 = await decide(env, MID_QUESTION, UserChoice("preset", preset="saver"))
    assert d2.assessment.source == "skipped" and env.fake.calls == []
    assert d2.plan == "medium" and d2.escalate_to is None  # 省钱：不升级


async def test_preset_balanced_escalates(env):
    d = await decide(env, MID_QUESTION, UserChoice("preset", preset="balanced"))
    assert d.plan == "medium" and d.escalate_to == "hard"


async def test_unknown_preset(env):
    with pytest.raises(RoutingError, match="未知的预设"):
        await decide(env, choice=UserChoice("preset", preset="luxury"))


async def test_manual_exact_models(env):
    d = await decide(env, "1+1=?", UserChoice("manual", members=("f1", "f4"), coordinator="b3"))
    assert d.lineup.members == ("f1", "f4") and d.lineup.coordinator == "b3"
    assert d.plan is None and d.escalate_to is None
    assert env.fake.calls == []


async def test_manual_single_model_is_solo(env):
    d = await decide(env, MID_QUESTION, UserChoice("manual", members=("f2",)))
    assert d.lineup.members == ("f2",) and d.lineup.pipeline == ("answer", "reveal")


async def test_manual_warns_but_obeys_when_tags_missing(env):
    d = await decide(
        env, "图里是什么", UserChoice("manual", members=("b3",)), attachments=("image",)
    )
    assert d.lineup.members == ("b3",)
    assert d.warnings and "vision" in d.warnings[0]


async def test_manual_rejects_unavailable_models():
    env = Env(app_config(disabled=["f4"]), FakeProvider("c"))
    with pytest.raises(RoutingError, match="f4"):
        await decide(env, choice=UserChoice("manual", members=("f1", "f4")))
    with pytest.raises(RoutingError, match="ghost"):
        await decide(env, choice=UserChoice("manual", members=("ghost",)))


async def test_manual_too_many(env):
    with pytest.raises(RoutingError, match="最多"):
        await decide(env, choice=UserChoice("manual", members=("f1", "f2", "f3", "f4", "f5")))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "preset"},
        {"mode": "manual"},
        {"mode": "auto", "preset": "saver"},
        {"mode": "auto", "members": ("f1",)},
        {"mode": "manual", "members": ("f1", "f1")},
        {"mode": "manual", "members": ("f1",), "coordinator": "f1"},
    ],
)
def test_invalid_choices(kwargs):
    with pytest.raises(RoutingError):
        UserChoice(**kwargs)


# --- 花费与确认 -----------------------------------------------------------------


async def test_options_cover_all_plans_and_chosen_matches(env):
    env.fake.queue("b1", planner_reply("medium"))
    d = await decide(env)
    assert set(d.options) == {"simple", "medium", "hard"}
    assert d.options["medium"].estimate.total_usd == d.estimate.total_usd
    costs = [d.options[p].estimate.total_usd for p in ("simple", "medium", "hard")]
    assert costs == sorted(costs)


async def test_confirmation_threshold():
    cheap = Env(app_config(confirm_threshold_usd=1.0), FakeProvider("c"))
    d = await decide(cheap, choice=UserChoice("preset", preset="strongest"))
    assert not d.needs_confirmation
    strict = Env(app_config(confirm_threshold_usd=0.0001), FakeProvider("c"))
    d = await decide(strict, "1+1=?")
    assert d.needs_confirmation and d.confirm_threshold_usd == 0.0001


async def test_seed_reproducible(env):
    a = await decide(env, "1+1=?", UserChoice("preset", preset="strongest"), seed=9)
    b = await decide(env, "1+1=?", UserChoice("preset", preset="strongest"), seed=9)
    assert a.lineup == b.lineup and a.estimate == b.estimate


# --- 记录 -----------------------------------------------------------------------


async def test_record_contents_and_outcome(env):
    env.fake.queue("b1", planner_reply("medium", reason="多步计算"))
    q = Question(MID_QUESTION)
    d = await route_question(
        q, None, config=env.config, router=env.router, prompts=env.prompts, seed=3
    )
    r = d.record(q)
    assert (r.mode, r.difficulty, r.difficulty_source, r.plan) == (
        "auto",
        "medium",
        "model",
        "medium",
    )
    assert r.assessment_reason == "多步计算" and r.planner_model == "b1" and r.planner_cost_usd > 0
    assert r.members == d.lineup.members and r.estimated_cost_usd == d.estimate.total_usd
    assert r.actual_cost_usd is None and not r.escalated
    done = r.with_outcome(actual_cost_usd=0.012, user_confirmed=None)
    assert done.actual_cost_usd == 0.012 and not done.escalated
    json.dumps(done.to_dict(), ensure_ascii=False)  # 可序列化入库
