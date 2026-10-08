"""无品牌偏好：同档位内均匀随机；把所有厂商和 id 换名，结果在位置上完全一致。"""

from __future__ import annotations

import random
from collections import Counter

from roundtable.core.providers import FakeProvider
from roundtable.core.routing import LineupBuilder, Question, UserChoice, route_question

from .conftest import POOL, Env, app_config


def available(cfg):
    return Env(cfg, FakeProvider("c")).router.available_models()


def test_uniform_within_tier_without_task_preference():
    cfg = app_config(prefer_task_tags=False, prefer_distinct_vendors=False)
    models = available(cfg)
    counts = Counter()
    for seed in range(3000):
        lineup = LineupBuilder(cfg, models, random.Random(seed)).build(cfg.routing.plans["simple"])
        counts[lineup.members[0]] += 1
    assert set(counts) == {"b1", "b2", "b3"}
    for c in counts.values():
        assert 850 < c < 1150, counts


def renamed_pool():
    """所有 id 和厂商名换掉，档位、标签、价格不变；顺序也打乱。"""
    pool = [
        (f"x{i}", f"Brand{9 - int(v[1])}", t, tags, pi, po)
        for i, (_, v, t, tags, pi, po) in enumerate(POOL)
    ]
    return pool


async def decide(cfg, choice, seed):
    env = Env(cfg, FakeProvider("c"))
    return await route_question(
        Question("一道题目"), choice, config=cfg, router=env.router, prompts=env.prompts, seed=seed
    )


async def test_renaming_does_not_change_decisions():
    original, renamed = app_config(), app_config(pool=renamed_pool())
    rename = {p[0]: r[0] for p, r in zip(POOL, renamed_pool(), strict=True)}
    for preset in ("saver", "balanced", "strongest"):
        for seed in range(30):
            choice = UserChoice("preset", preset=preset)
            a = await route_question(
                Question("一道题目"),
                choice,
                config=original,
                router=Env(original, FakeProvider("c")).router,
                prompts=Env(original, FakeProvider("c")).prompts,
                seed=seed,
            )
            b = await route_question(
                Question("一道题目"),
                choice,
                config=renamed,
                router=Env(renamed, FakeProvider("c")).router,
                prompts=Env(renamed, FakeProvider("c")).prompts,
                seed=seed,
            )
            assert tuple(rename[m] for m in a.lineup.members) == b.lineup.members
            assert rename.get(a.lineup.coordinator) == b.lineup.coordinator
            assert a.estimate.total_usd == b.estimate.total_usd
