"""路由入口：档位全员 / 自选、答案长度判断、确认门槛、各档位花费、记录。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.providers import ErrorKind, FakeProvider
from roundtable.core.routing import CUSTOM, Question, RoutingError, UserChoice, route_question

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
    return {by[i].tier for i in ids}


# --- 档位 -----------------------------------------------------------------------


async def test_default_is_budget_tier_all_seated(env):
    d = await decide(env, "1+1=?")
    assert d.plan == "budget" and d.escalate_to == "flagship"
    assert {*d.lineup.members, d.lineup.coordinator} == {"b1", "b2", "b3"}
    assert tiers(env, d.lineup.members) == {"budget"}


async def test_flagship_tier(env):
    d = await decide(env, "1+1=?", UserChoice("flagship"))
    assert d.plan == "flagship" and d.escalate_to is None
    assert len(d.lineup.members) == 4 and tiers(env, d.lineup.members) == {"flagship"}


async def test_difficulty_only_changes_estimate_not_people(env):
    short = await decide(env, "1+1=?")
    env.fake.queue("b1", planner_reply("hard"))
    long = await decide(env)
    assert len(short.lineup.members) == len(long.lineup.members)
    assert long.estimate.total_usd > short.estimate.total_usd


async def test_rule_decides_length_without_calling_model(env):
    d = await decide(env, "1+1=?")
    assert d.assessment.source == "rule" and d.assessment.difficulty == "simple"
    assert env.fake.calls == []
    assert d.planner_cost_usd == 0


async def test_planner_called_when_rules_cannot_decide(env):
    env.fake.queue("b1", planner_reply("hard"))
    d = await decide(env)
    assert d.assessment.source == "model" and d.assessment.difficulty == "hard"
    assert d.assessment.planner.model_id == "b1"  # 最便宜的便宜档
    assert d.planner_cost_usd > 0


async def test_planner_failure_uses_default(env):
    env.fake.queue("b1", ErrorKind.QUOTA)
    d = await decide(env)
    assert d.assessment.source == "default"
    assert d.assessment.difficulty == env.config.routing.default_difficulty
    assert "规划员不可用" in d.assessment.reason


async def test_planner_answer_length_used_and_clamped(env):
    env.fake.queue("b1", planner_reply("medium", tokens=10**6))
    d = await decide(env)
    answer = next(s for s in d.estimate.steps if s.step == "answer")
    bounds = env.config.routing.estimate.answer_tokens_bounds
    cap = env.config.roundtable.step_params["answer"]["max_tokens"]
    assert bounds.max > cap  # 规划员的估计被限制在范围内，单次输出又不超过该步骤的上限
    # 能用工具的步骤按 estimate.tool_rounds 多估一些调用
    rounds = 1 + env.config.routing.estimate.tool_rounds
    assert answer.output_tokens == round(cap * rounds) * len(d.lineup.members)


async def test_image_question_still_seats_everyone(env):
    """有图片也不减人：没有 vision 的成员以后收到文字版（阶段 14）。"""
    d = await decide(env, "这张图里的函数是什么？", attachments=("image",))
    assert d.assessment.require_tags == ("vision",)
    assert {*d.lineup.members, d.lineup.coordinator} == {"b1", "b2", "b3"}


async def test_unknown_tier(env):
    with pytest.raises(RoutingError, match="未知的档位"):
        await decide(env, "1+1=?", UserChoice("giant"))


async def test_tier_with_too_few_models_is_clear_error():
    env = Env(app_config(disabled=("b3",)), FakeProvider("c"))
    with pytest.raises(RoutingError, match="至少需要 3 个"):
        await decide(env, "1+1=?")


# --- 自选 -----------------------------------------------------------------------


async def test_custom_exact_models(env):
    d = await decide(env, "1+1=?", UserChoice(CUSTOM, ("b1", "f1", "f2"), "f2"))
    assert d.plan == CUSTOM and d.escalate_to is None
    assert d.lineup.members == ("b1", "f1") and d.lineup.coordinator == "f2"
    assert d.options[CUSTOM].estimate == d.estimate


async def test_custom_rejects_unavailable_models():
    env = Env(app_config(disabled=("f5",)), FakeProvider("c"))
    with pytest.raises(RoutingError, match="f5"):
        await decide(env, "1+1=?", UserChoice(CUSTOM, ("f1", "f2", "f5")))


async def test_custom_too_few(env):
    with pytest.raises(RoutingError, match="至少需要"):
        await decide(env, "1+1=?", UserChoice(CUSTOM, ("f1", "f2")))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"tier": CUSTOM},
        {"tier": "budget", "models": ("f1",)},
        {"tier": "budget", "coordinator": "f1"},
        {"tier": CUSTOM, "models": ("f1", "f1")},
        {"tier": CUSTOM, "models": ("f1", "f2", "f3"), "coordinator": "f4"},
    ],
)
def test_invalid_choices(kwargs):
    with pytest.raises(RoutingError):
        UserChoice(**kwargs)


def test_choice_roundtrip_and_old_sessions():
    c = UserChoice(CUSTOM, ("f1", "f2", "f3"), "f1")
    assert UserChoice.from_dict(c.to_dict()) == c
    with pytest.raises(RoutingError, match="旧版本"):
        UserChoice.from_dict({"mode": "auto", "preset": None})


# --- 花费与确认 -----------------------------------------------------------------


async def test_options_cover_all_tiers_and_chosen_matches(env):
    d = await decide(env, "1+1=?")
    assert set(d.options) == {"budget", "flagship"}
    assert d.options["budget"].estimate == d.estimate
    assert d.options["budget"].lineup == d.lineup
    assert d.options["flagship"].estimate.total_usd > d.estimate.total_usd


async def test_option_unavailable_when_tier_too_small():
    env = Env(app_config(disabled=("f2", "f3", "f4", "f5")), FakeProvider("c"))
    d = await decide(env, "1+1=?")
    assert not d.options["flagship"].available and "至少需要" in d.options["flagship"].reason


async def test_review_estimate_uses_reviews_per_answer():
    """组员多时每人只评 k 份：预估随 k 变化，与组员数的平方无关。"""
    cfg = app_config()
    env = Env(cfg, FakeProvider("c"))
    full = await decide(env, "1+1=?", UserChoice("flagship"))
    rt = cfg.roundtable.model_copy(update={"reviews_per_answer": 1})
    small = Env(cfg.model_copy(update={"roundtable": rt}), FakeProvider("c"))
    one = await decide(small, "1+1=?", UserChoice("flagship"))
    review = {
        d: next(s for s in x.estimate.steps if s.step == "review")
        for d, x in (("full", full), ("one", one))
    }
    assert review["one"].input_tokens < review["full"].input_tokens


async def test_confirmation_threshold():
    cheap = Env(app_config(confirm_threshold_usd=5.0), FakeProvider("c"))
    d = await decide(cheap, "1+1=?", UserChoice("flagship"))
    assert not d.needs_confirmation
    strict = Env(app_config(confirm_threshold_usd=0.0001), FakeProvider("c"))
    d = await decide(strict, "1+1=?")
    assert d.needs_confirmation and d.confirm_threshold_usd == 0.0001


async def test_seed_reproducible(env):
    a = await decide(env, "1+1=?", UserChoice("flagship"), seed=9)
    b = await decide(env, "1+1=?", UserChoice("flagship"), seed=9)
    assert a.lineup == b.lineup and a.estimate == b.estimate


# --- 记录 -----------------------------------------------------------------------


async def test_record_contents_and_outcome(env):
    env.fake.queue("b1", planner_reply("medium", reason="多步计算"))
    q = Question(MID_QUESTION)
    d = await route_question(
        q, None, config=env.config, router=env.router, prompts=env.prompts, seed=3
    )
    r = d.record(q)
    assert (r.difficulty, r.difficulty_source, r.plan) == ("medium", "model", "budget")
    assert r.assessment_reason == "多步计算" and r.planner_model == "b1" and r.planner_cost_usd > 0
    assert r.members == d.lineup.members and r.coordinator == d.lineup.coordinator
    assert r.absent == () and r.estimated_cost_usd == d.estimate.total_usd
    assert r.actual_cost_usd is None and not r.escalated
    done = r.with_outcome(actual_cost_usd=0.012, user_confirmed=None)
    assert done.actual_cost_usd == 0.012 and not done.escalated
    json.dumps(done.to_dict(), ensure_ascii=False)  # 可序列化入库


# --- 协同模式 -------------------------------------------------------------------


async def test_collab_uses_collab_pipeline_and_estimates_every_step(env):
    d = await decide(env, "1+1=?", UserChoice(workflow="collab"))
    assert d.lineup.pipeline == tuple(env.config.roundtable.collab_pipeline)
    steps = {s.step: s for s in d.estimate.steps}
    assert d.estimate.unknown_steps == ()
    for step in ("decompose", "volunteer", "assign", "work", "cross_review", "rework", "merge"):
        assert steps[step].cost_usd > 0, step
    assert d.options["flagship"].lineup.pipeline == d.lineup.pipeline
    assert d.record(Question("1+1=?")).workflow == "collab"
    custom = await decide(env, "1+1=?", UserChoice(CUSTOM, ("b1", "b2", "f1"), workflow="collab"))
    assert custom.lineup.pipeline == d.lineup.pipeline


def test_unknown_workflow():
    with pytest.raises(RoutingError, match="未知的模式"):
        UserChoice(workflow="debate")
    assert UserChoice.from_dict({"tier": "budget"}).workflow == "discussion"
