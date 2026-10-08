"""编排引擎：完整流程、确认点、预算、升级、组员退出、中断恢复。全部使用 Fake provider。"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from roundtable.core.orchestrator import OrchestratorError
from roundtable.core.providers import ErrorKind
from roundtable.core.routing import Question, UserChoice

from .conftest import MEDIUM, SHORT, Env

ANSWER = "独立完成同一道题"  # 作答提示词中的片段，用于统计调用


def record(env: Env, sid: str) -> dict:
    return env.rt.repo.routing_record(sid)


# --- 完整流程 -------------------------------------------------------------------


async def test_simple_question_single_member(env):
    r = await env.orc.start(Question(SHORT), seed=1)
    assert r.status == "completed" and r.checkpoint is None
    assert len(env.models_called(ANSWER)) == 1
    assert r.final_answer.endswith("的答案：最大值 2，最小值 -2。")
    rec = record(env, r.session_id)
    assert rec["plan"] == "simple" and rec["actual_cost_usd"] == pytest.approx(r.cost_usd)
    assert not rec["escalated"]


async def test_medium_question_small_table(env):
    r = await env.orc.start(Question(MEDIUM), seed=2)
    assert r.status == "completed"
    assert r.final_answer == "最大值 2，最小值 -2"
    members = env.models_called(ANSWER)
    assert env.tiers(members) == {"budget"} and len(members) == 2
    steps = env.rt.repo.completed_steps(r.session_id, 0)
    assert steps == ["answer", "review", "revise", "synthesize", "reveal"]
    rec = record(env, r.session_id)
    assert rec["difficulty_source"] == "model" and not rec["escalated"]
    assert rec["planner_cost_usd"] > 0


async def test_events_have_no_model_identity(env):
    await env.orc.start(Question(MEDIUM), seed=2)
    text = json.dumps([asdict(e) for _, e in env.events], ensure_ascii=False, default=str)
    for m in env.config.models.models:
        assert f'"{m.id}"' not in text
    types = [e.type for _, e in env.events]
    assert types[0] == "routed" and types[-1] == "completed"
    assert "step_started" in types and "call_done" in types


# --- 升级 ---------------------------------------------------------------------


async def test_auto_escalation_when_cheap_enough():
    env = Env(resolved=False, confirm_threshold_usd=100.0)
    r = await env.orc.start(Question(MEDIUM), seed=3)
    assert r.status == "completed"
    tables = env.rt.repo.tables(r.session_id)
    assert [t["status"] for t in tables] == ["done", "done"]
    assert env.tiers(tables[1]["members"].values()) == {"flagship"}
    rec = record(env, r.session_id)
    assert rec["escalated"] and rec["escalated_plan"] == "hard"
    assert "分歧" in rec["escalation_reason"]


async def test_escalation_needs_confirmation_then_accept():
    env = Env(resolved=False, confirm_threshold_usd=0.0001)
    # 自动模式的小圆桌本身也会超门槛，先确认
    r = await env.orc.start(Question(MEDIUM), seed=4)
    assert r.checkpoint.kind == "cost"
    r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "escalation"
    assert r.checkpoint.card["options"][1]["key"] == "accept"
    r = await env.orc.respond(r.session_id, "accept")
    assert r.status == "completed" and r.final_answer == "最大值 2，最小值 -2"
    rec = record(env, r.session_id)
    assert not rec["escalated"] and rec["escalation_reason"]  # 提出过但未升级
    assert [t["status"] for t in env.rt.repo.tables(r.session_id)] == ["done", "skipped"]


async def test_escalation_confirmed_runs_flagship_table():
    env = Env(resolved=False, confirm_threshold_usd=0.0001)
    r = await env.orc.start(Question(MEDIUM), seed=4)
    r = await env.orc.respond(r.session_id, "continue")
    r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "completed"
    assert record(env, r.session_id)["escalated"]


async def test_saver_preset_never_escalates():
    env = Env(resolved=False, confirm_threshold_usd=100.0)
    r = await env.orc.start(Question(MEDIUM), UserChoice("preset", preset="saver"), seed=5)
    assert r.status == "completed" and len(env.rt.repo.tables(r.session_id)) == 1


# --- 单题花费确认 ---------------------------------------------------------------


async def test_cost_confirmation_continue():
    env = Env(confirm_threshold_usd=0.0001)
    r = await env.orc.start(Question(SHORT), UserChoice("preset", preset="strongest"), seed=6)
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "cost"
    assert env.models_called(ANSWER) == []  # 确认前不开始作答
    card = r.checkpoint.card
    assert {"continue", "stop", "plan:simple", "plan:medium"} <= {o["key"] for o in card["options"]}
    r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "completed"
    assert env.tiers(env.models_called(ANSWER)) == {"flagship"}
    assert record(env, r.session_id)["user_confirmed"] is True


async def test_cost_confirmation_switch_plan():
    env = Env(confirm_threshold_usd=0.0001)
    r = await env.orc.start(Question(SHORT), UserChoice("preset", preset="strongest"), seed=6)
    simple_cost = next(
        o["cost_usd"] for o in r.checkpoint.card["options"] if o["key"] == "plan:simple"
    )
    r = await env.orc.respond(r.session_id, "plan:simple")
    assert r.status == "completed"
    assert len(env.models_called(ANSWER)) == 1
    rec = record(env, r.session_id)
    assert rec["plan"] == "simple" and rec["estimated_cost_usd"] == pytest.approx(simple_cost)
    assert rec["extra"]["switched_by_user"]


async def test_cost_confirmation_stop():
    env = Env(confirm_threshold_usd=0.0001)
    r = await env.orc.start(Question(SHORT), UserChoice("preset", preset="strongest"), seed=6)
    r = await env.orc.respond(r.session_id, "stop")
    assert r.status == "stopped" and env.models_called(ANSWER) == []


async def test_invalid_responses(env):
    r = await env.orc.start(Question(SHORT), seed=1)
    with pytest.raises(OrchestratorError, match="没有待回复"):
        await env.orc.respond(r.session_id, "continue")
    env2 = Env(confirm_threshold_usd=0.0001)
    r2 = await env2.orc.start(Question(SHORT), seed=1)
    with pytest.raises(OrchestratorError, match="无效的选项"):
        await env2.orc.respond(r2.session_id, "maybe")


# --- 预算 ---------------------------------------------------------------------


def exhaust_month(env: Env) -> None:
    from roundtable.core.providers import Completion, Message

    sid = env.rt.repo.create_session("旧题", seed=0)
    c = Completion("x", "b1", "c", "aggregator", "b1", 1, 1, 0, 20.0, "reported", 0.1)
    env.rt.repo.record_call(
        sid,
        step="answer",
        role="member",
        model_id="b1",
        messages=[Message("user", "q")],
        completion=c,
    )


async def test_budget_exhausted_blocks_before_routing():
    env = Env()
    exhaust_month(env)
    r = await env.orc.start(Question(MEDIUM), seed=7)
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "budget"
    assert env.fake.calls == []  # 连规划员都没调用
    r = await env.orc.respond(r.session_id, "continue")  # 用户明确选择超出预算继续
    assert r.status == "completed"
    assert env.rt.repo.budget_override(r.session_id)


async def test_budget_exhausted_stop():
    env = Env()
    exhaust_month(env)
    r = await env.orc.start(Question(MEDIUM), seed=7)
    r = await env.orc.respond(r.session_id, "stop")
    assert r.status == "stopped" and env.fake.calls == []


async def test_daily_cap_pauses_mid_run():
    env = Env()
    budget = env.config.roundtable.budget.model_copy(update={"daily_usd": 0.0005})
    env.rt.budget.policy = budget
    r = await env.orc.start(Question(MEDIUM), seed=8)
    assert r.status == "paused" and r.checkpoint.kind == "budget"
    assert r.checkpoint.card["details"]["blocked_by"] == ["day"]
    assert "step" in r.checkpoint.card["details"]
    r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "completed"


# --- 组员退出 -----------------------------------------------------------------


async def test_too_few_members_asks_then_continues():
    env = Env(confirm_threshold_usd=100.0)
    r = await env.orc.start(
        Question(SHORT), UserChoice("manual", members=("b1", "b2", "b3"), coordinator="f1"), seed=9
    )
    assert r.status == "completed"  # 正常情况
    env2 = Env(confirm_threshold_usd=100.0)
    env2.fake.queue("b2", *[ErrorKind.SERVER] * 4)
    env2.fake.queue("b3", *[ErrorKind.SERVER] * 4)
    r = await env2.orc.start(
        Question(SHORT), UserChoice("manual", members=("b1", "b2", "b3"), coordinator="f1"), seed=9
    )
    assert r.status == "paused" and r.checkpoint.kind == "members"
    r = await env2.orc.respond(r.session_id, "continue")
    assert r.status == "completed" and r.final_answer


async def test_all_members_fail():
    env = Env(confirm_threshold_usd=100.0)
    for m in ("b1", "b2"):
        env.fake.queue(m, *[ErrorKind.SERVER] * 4)
    r = await env.orc.start(
        Question(SHORT), UserChoice("manual", members=("b1", "b2"), coordinator="f1"), seed=9
    )
    assert r.status == "failed"


async def test_routing_failure_marks_failed():
    env = Env()
    r = await env.orc.start(Question(SHORT), UserChoice("manual", members=("b1", "ghost")), seed=1)
    assert r.status == "failed"


# --- 中断与恢复 ---------------------------------------------------------------


class Crash(Exception):
    pass


async def test_resume_after_crash_does_not_repeat_calls():
    env = Env(confirm_threshold_usd=100.0)
    crashed = {"done": False}
    original = env.reply

    def flaky(model, messages):
        if "根据审阅意见修订" in messages[0].content and not crashed["done"]:
            crashed["done"] = True
            raise Crash("进程中断")
        return original(model, messages)

    env.fake._default = flaky
    with pytest.raises(Crash):
        await env.orc.start(Question(MEDIUM), seed=10)
    [summary] = env.rt.repo.list_sessions()
    sid = summary.id
    assert env.rt.repo.completed_steps(sid, 0) == ["answer", "review"]
    answers_before = len(env.models_called(ANSWER))

    r = await env.orc.resume(sid)
    assert r.status == "completed"
    assert len(env.models_called(ANSWER)) == answers_before  # 作答没有重复


async def test_resume_reproduces_same_orders():
    """同一 seed：中断后恢复的执行，与不中断时的抽座、代号、乱序完全一致。"""
    a = Env(confirm_threshold_usd=100.0)
    r = await a.orc.start(Question(MEDIUM), seed=11)
    straight = [(c.model, c.messages[-1].content) for c in a.fake.calls]

    b = Env(confirm_threshold_usd=100.0)
    crashed = {"done": False}
    original = b.reply

    def flaky(model, messages):
        if "根据审阅意见修订" in messages[0].content and not crashed["done"]:
            crashed["done"] = True
            raise Crash()
        return original(model, messages)

    b.fake._default = flaky
    with pytest.raises(Crash):
        await b.orc.start(Question(MEDIUM), seed=11)
    await b.orc.resume(b.rt.repo.list_sessions()[0].id)
    resumed = [(c.model, c.messages[-1].content) for c in b.fake.calls]
    # 去掉中断时那次没完成的调用后应完全一致（并发调用的先后可能不同，按集合比较）
    assert sorted(set(resumed)) == sorted(set(straight))
    assert r.status == "completed"


async def test_resume_finished_session_is_noop(env):
    r = await env.orc.start(Question(SHORT), seed=1)
    calls = len(env.fake.calls)
    r2 = await env.orc.resume(r.session_id)
    assert r2.status == "completed" and len(env.fake.calls) == calls
    assert r2.final_answer == r.final_answer


async def test_reveal(env):
    r = await env.orc.start(Question(SHORT), seed=1)
    assert not env.rt.repo.session_view(r.session_id).revealed
    env.orc.reveal_identities(r.session_id)
    view = env.rt.repo.session_view(r.session_id)
    assert view.revealed and all(s.model_id for s in view.seats)
