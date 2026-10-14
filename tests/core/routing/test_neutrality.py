"""无品牌偏好：把所有厂商和 id 换名，结果在位置上完全一致（统筹均匀随机见 test_lineup）。"""

from __future__ import annotations

from roundtable.core.providers import FakeProvider
from roundtable.core.routing import Question, UserChoice, route_question

from .conftest import POOL, Env, app_config


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
    for tier in ("budget", "flagship"):
        for seed in range(30):
            choice = UserChoice(tier)
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
            assert tuple(rename[m] for m in a.lineup.absent) == b.lineup.absent
