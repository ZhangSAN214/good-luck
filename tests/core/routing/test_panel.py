"""分诊小组（R15）：选人只看价格、聚合规则、规则跳过、失败退回默认。"""

from __future__ import annotations

import json
import random

from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import FakeProvider
from roundtable.core.routing import PanelVote, Question, aggregate, run_panel
from roundtable.core.routing.panel import pick_panel

from .conftest import Env, app_config

LONG = "请写一份关于钢的热处理工艺的完整报告，包含多个部分的分析与比较，并给出方案。" * 3


def vote(workflow="discussion", difficulty="medium", reason="需要几步推理"):
    return PanelVote(difficulty=difficulty, workflow=workflow, reason=reason)


def env_with(replies):
    """replies：按调用顺序返回；给 None 表示输出无法解析。"""
    cfg = app_config()
    fake = FakeProvider("c", default=lambda m, msgs: next(replies))
    return cfg, Env(cfg, fake)


def panel_json(workflow, difficulty="medium", reason="理由"):
    return json.dumps({"difficulty": difficulty, "workflow": workflow, "reason": reason})


async def run(cfg, env, text=LONG, seed=1):
    return await run_panel(
        Question(text),
        config=cfg,
        router=env.router,
        prompts=PromptLibrary(),
        available=env.router.available_models(),
        rng=random.Random(seed),
    )


def test_panel_members_are_the_cheapest_by_price():
    cfg = app_config()
    avail = Env(cfg, FakeProvider("c")).router.available_models()
    for seed in range(20):
        picked = pick_panel(cfg, avail, random.Random(seed))
        assert [m.id for m in picked] == ["b1", "b2", "b3"]  # 便宜档按价格，全部取到
    small = cfg.model_copy(
        update={
            "routing": cfg.routing.model_copy(
                update={"panel": cfg.routing.panel.model_copy(update={"size": 2})}
            )
        }
    )
    assert [m.id for m in pick_panel(small, avail, random.Random(0))] == ["b1", "b2"]


def test_aggregate_majority_tie_goes_to_cheaper_and_median_difficulty():
    cfg = app_config()
    assert aggregate([vote("solo"), vote("collab"), vote("collab")], cfg)[0] == "collab"
    # 三种各一票 / 两两平票：取更便宜的
    assert aggregate([vote("solo"), vote("discussion"), vote("collab")], cfg)[0] == "solo"
    assert aggregate([vote("collab"), vote("discussion")], cfg)[0] == "discussion"
    votes = [vote(difficulty="hard"), vote(difficulty="hard"), vote(difficulty="simple")]
    assert aggregate(votes, cfg) == ("discussion", "flagship", "hard")
    assert aggregate([vote(difficulty="simple")], cfg)[1:] == ("budget", "simple")


async def test_panel_majority_vote_costs_something_and_is_recorded_per_call():
    replies = iter([panel_json("collab", "hard"), panel_json("collab", "hard"), panel_json("solo")])
    cfg, env = env_with(replies)
    result = await run(cfg, env)
    assert (result.workflow, result.tier, result.source) == ("collab", "flagship", "panel")
    assert len(result.calls) == 3 and len(result.votes) == 3
    assert result.cost_usd == sum(c.completion.cost_usd for c in result.calls)
    # 同一份请求发给每位成员；输出短
    sent = {tuple((m.role, m.content) for m in c.messages) for c in result.calls}
    assert len(sent) == 1
    assert {m for m in env.fake.calls[0].params} >= {"max_tokens"}


async def test_simple_question_by_rules_skips_the_panel():
    cfg, env = env_with(iter([]))
    result = await run(cfg, env, "1+1=?")
    assert (result.workflow, result.tier, result.source) == ("solo", "budget", "rules")
    assert result.calls == () and result.cost_usd == 0 and env.fake.calls == []


async def test_unparsable_votes_are_ignored_and_all_bad_falls_back_to_default():
    cfg, env = env_with(iter(["不是 JSON", panel_json("solo", "simple"), "{坏的"]))
    result = await run(cfg, env)
    assert result.source == "panel" and len(result.votes) == 1 and result.workflow == "solo"
    assert len(env.fake.calls) == 3  # 解析失败不重试，省钱

    cfg, env = env_with(iter(["a", "b", "c"]))
    result = await run(cfg, env)
    assert (result.source, result.workflow) == ("default", "discussion")
    assert "没有给出有效建议" in result.note and result.cost_usd > 0


def test_aggregate_can_recommend_brainstorm_and_it_ranks_below_solo():
    cfg = app_config()
    votes = [vote("brainstorm"), vote("brainstorm"), vote("discussion")]
    assert aggregate(votes, cfg)[0] == "brainstorm"
    assert aggregate([vote("brainstorm"), vote("discussion")], cfg)[0] == "brainstorm"
    assert aggregate([vote("brainstorm"), vote("solo")], cfg)[0] == "solo"
