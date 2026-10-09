from __future__ import annotations

import random

from roundtable.core.providers import ErrorKind, FakeProvider
from roundtable.core.routing import pick_planner_model, run_planner

from .conftest import POOL, Env, app_config, planner_reply


async def plan(env: Env, question="一道题"):
    return await run_planner(
        question,
        config=env.config,
        router=env.router,
        prompts=env.prompts,
        available=env.router.available_models(),
        rng=random.Random(0),
    )


def test_picks_cheapest_budget_model(env):
    m = pick_planner_model(env.config, env.router.available_models(), random.Random(0))
    assert m.id == "b1"


def test_skips_unavailable_cheapest():
    cfg = app_config(disabled=["b1"])
    env = Env(cfg, FakeProvider("c"))
    assert pick_planner_model(cfg, env.router.available_models(), random.Random(0)).id == "b2"


def test_no_budget_models():
    pool = [p for p in POOL if p[2] == "flagship"]
    cfg = app_config(pool=pool)
    assert (
        pick_planner_model(
            cfg, Env(cfg, FakeProvider("c")).router.available_models(), random.Random(0)
        )
        is None
    )


async def test_success(env):
    env.fake.queue("b1", planner_reply("hard", "math", 3000, "多步证明"))
    r = await plan(env, "证明某定理")
    assert r.ok and r.model_id == "b1" and r.calls == 1
    assert (r.output.difficulty, r.output.task_type, r.output.expected_answer_tokens) == (
        "hard",
        "math",
        3000,
    )
    assert r.cost_usd > 0 and r.prompt_version == "v1" and r.prompt_sha256
    sent = env.fake.calls[0]
    assert "证明某定理" in sent.messages[-1].content
    assert sent.params == {
        "max_tokens": env.config.routing.planner.max_tokens,
        "reasoning": {"effort": "low"},
    }


async def test_unknown_task_type_becomes_other(env):
    env.fake.queue("b1", planner_reply(task_type="cooking"))
    assert (await plan(env)).output.task_type == "other"


async def test_fenced_json_accepted(env):
    env.fake.queue("b1", "```json\n" + planner_reply("simple") + "\n```")
    assert (await plan(env)).output.difficulty == "simple"


async def test_bad_output_retried_once_then_ok(env):
    env.fake.queue("b1", "我觉得是中等难度", planner_reply("medium"))
    r = await plan(env)
    assert r.ok and r.calls == 2


async def test_bad_output_twice_fails_gracefully(env):
    env.fake.queue("b1", "不是 JSON", '{"difficulty": "extreme"}')
    r = await plan(env)
    assert not r.ok and r.calls == 2 and "无法解析" in r.error
    assert r.cost_usd > 0  # 失败的调用也计费


async def test_channel_failure_does_not_raise(env):
    env.fake.queue("b1", ErrorKind.QUOTA)
    r = await plan(env)
    assert not r.ok and "调用失败" in r.error and r.calls == 0
