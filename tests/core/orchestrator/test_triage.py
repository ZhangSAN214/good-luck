"""自动模式（R15）：分诊小组给建议 → 确认卡片 → 用户采用或改选 → 按所选方式运行。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.routing import Question, RoutingError, UserChoice

from .conftest import MEDIUM, SHORT, Env
from .test_media import script

PANEL = "分诊小组的一员"


def panel_env(workflow="solo", difficulty="simple", **kw):
    env = Env(confirm_threshold_usd=100.0, **kw)
    script(
        env,
        **{
            PANEL: lambda m, msgs: json.dumps(
                {"difficulty": difficulty, "workflow": workflow, "reason": "题目简短明确"}
            )
        },
    )
    return env


async def start(env, text=MEDIUM, **kw):
    return await env.orc.start(Question(text), UserChoice(workflow="auto"), seed=2, **kw)


async def test_auto_asks_panel_then_waits_for_user_before_doing_anything():
    env = panel_env()
    r = await start(env)
    assert r.status == "awaiting_confirmation"
    card = r.checkpoint.card
    assert card["kind"] == "triage" and card["recommendation"] == "continue"
    assert card["details"]["workflow"] == "solo" and card["details"]["tier"] == "budget"
    keys = [o["key"] for o in card["options"]]
    assert keys[0] == "continue" and "mode:collab:flagship" in keys and keys[-1] == "stop"
    assert all(o["cost_usd"] is not None for o in card["options"][:-1])
    assert env.models_called("独立完成同一道题") == []  # 还没有任何人开始作答
    assert env.rt.repo.session_cost(r.session_id) > 0  # 小组的花费计入本题
    assert env.rt.repo.tables(r.session_id) == []
    assert env.rt.repo.session_row(r.session_id)["workflow"] == "auto"


async def test_accepting_runs_the_recommended_mode():
    env = panel_env("solo")
    r = await start(env)
    r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "completed" and r.final_answer
    row = env.rt.repo.session_row(r.session_id)
    assert row["workflow"] == "solo" and row["choice"]["workflow"] == "solo"
    table = env.rt.repo.tables(r.session_id)[0]
    assert table["pipeline"] == ["answer", "reveal"] and table["coordinator"] is None


async def test_user_can_override_the_recommendation():
    env = panel_env("solo")
    r = await start(env)
    r = await env.orc.respond(r.session_id, "mode:discussion:budget")
    assert r.status == "completed"
    assert env.rt.repo.session_row(r.session_id)["workflow"] == "discussion"
    assert len(env.rt.repo.tables(r.session_id)[0]["members"]) == 2  # 便宜档全员（扣掉统筹）


async def test_invalid_override_is_rejected_and_stop_ends_without_running():
    env = panel_env("solo")
    r = await start(env)
    from roundtable.core.orchestrator import OrchestratorError

    with pytest.raises(OrchestratorError):
        await env.orc.respond(r.session_id, "mode:nonsense:budget")
    r = await env.orc.respond(r.session_id, "stop")
    assert r.status == "stopped" and env.models_called("独立完成同一道题") == []


async def test_simple_question_skips_panel_but_still_asks_the_user():
    env = panel_env("collab")  # 小组即使想选协同，规则判断简单题时不会被调用
    r = await start(env, SHORT)
    card = r.checkpoint.card
    assert card["details"]["source"] == "rules" and card["details"]["workflow"] == "solo"
    assert env.models_called(PANEL) == [] and env.rt.repo.session_cost(r.session_id) == 0


async def test_anonymous_card_has_no_model_identity():
    env = panel_env("discussion", "medium")
    r = await start(env, anonymous=True)
    text = json.dumps(r.checkpoint.card, ensure_ascii=False)
    for m in env.config.models.models:
        for term in (m.id, m.vendor):
            assert f"'{term}'" not in text and f'"{term}"' not in text
    view = str(env.rt.repo.session_view(r.session_id))
    assert "'b1'" not in view


def test_auto_cannot_combine_with_media_or_custom():
    with pytest.raises(RoutingError):
        UserChoice(workflow="auto", media="image")
    with pytest.raises(RoutingError):
        UserChoice("custom", ("b1", "b2"), None, "auto")
