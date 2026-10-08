"""编排引擎：完整流程、确认点、预算、升级、组员退出、中断恢复。全部使用 Fake provider。"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from roundtable.core.orchestrator import OrchestratorError
from roundtable.core.providers import ErrorKind
from roundtable.core.routing import Question, UserChoice

from .conftest import MEDIUM, SHORT, Env

ANSWER = "独立完成同一道题"  # 圆桌作答提示词中的片段，用于统计调用


def record(env: Env, sid: str) -> dict:
    return env.rt.repo.routing_record(sid)


# --- 完整流程 -------------------------------------------------------------------


async def test_short_question_seats_whole_budget_tier(env):
    r = await env.orc.start(Question(SHORT), seed=1)
    assert r.status == "completed" and r.checkpoint is None
    members = env.models_called(ANSWER)
    assert sorted(members) != [] and env.tiers(members) == {"budget"}
    table = env.rt.repo.tables(r.session_id)[0]
    seated = {*table["members"].values(), table["coordinator"]}
    assert seated == {"b1", "b2", "b3"}  # 便宜档全员上桌
    assert table["coordinator"] not in members  # 统筹不作答
    assert r.final_answer == "最大值 2，最小值 -2"
    rec = record(env, r.session_id)
    assert rec["plan"] == "budget" and rec["actual_cost_usd"] == pytest.approx(r.cost_usd)
    assert rec["difficulty_source"] == "rule" and rec["planner_cost_usd"] == 0
    assert not rec["escalated"]


async def test_medium_question_budget_table(env):
    r = await env.orc.start(Question(MEDIUM), seed=2)
    assert r.status == "completed"
    assert r.final_answer == "最大值 2，最小值 -2"
    members = env.models_called(ANSWER)
    assert env.tiers(members) == {"budget"} and len(members) == 2
    steps = env.rt.repo.completed_steps(r.session_id, 0)
    assert steps == ["answer", "review", "revise", "synthesize", "reveal"]
    rec = record(env, r.session_id)
    assert rec["difficulty_source"] == "model" and not rec["escalated"]
    assert rec["planner_cost_usd"] > 0  # 规则判断不出答案长度时才调用规划员


async def test_flagship_tier_seats_all_flagships():
    env = Env(confirm_threshold_usd=100.0)
    r = await env.orc.start(Question(SHORT), UserChoice("flagship"), seed=2)
    assert r.status == "completed"
    members = env.models_called(ANSWER)
    assert env.tiers(members) == {"flagship"} and len(members) == 4


async def test_custom_lineup_with_named_coordinator(env):
    r = await env.orc.start(Question(SHORT), UserChoice("custom", ("b1", "b2", "f1"), "f1"), seed=2)
    assert r.status == "completed"
    assert sorted(env.models_called(ANSWER)) == ["b1", "b2"]
    assert env.rt.repo.tables(r.session_id)[0]["coordinator"] == "f1"


async def test_events_have_no_model_identity(env):
    await env.orc.start(Question(MEDIUM), seed=2)
    text = json.dumps([asdict(e) for _, e in env.events], ensure_ascii=False, default=str)
    for m in env.config.models.models:
        assert f'"{m.id}"' not in text
    types = [e.type for _, e in env.events]
    assert types[0] == "routed" and types[-1] == "completed"
    assert "step_started" in types and "call_done" in types


# --- 升级 ---------------------------------------------------------------------


async def test_escalation_always_asks_even_when_cheap():
    env = Env(resolved=False, confirm_threshold_usd=100.0)
    r = await env.orc.start(Question(MEDIUM), seed=3)
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "escalation"
    assert [t["status"] for t in env.rt.repo.tables(r.session_id)] == ["done", "pending"]
    r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "completed"
    tables = env.rt.repo.tables(r.session_id)
    assert [t["status"] for t in tables] == ["done", "done"]
    seated = {*tables[1]["members"].values(), tables[1]["coordinator"]}
    assert env.tiers(seated) == {"flagship"} and len(seated) == 5
    rec = record(env, r.session_id)
    assert rec["escalated"] and rec["escalated_plan"] == "flagship"
    assert "分歧" in rec["escalation_reason"]


async def test_escalation_needs_confirmation_then_accept():
    env = Env(resolved=False, confirm_threshold_usd=0.0001)
    # 便宜档本身也超门槛，先确认
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


async def test_flagship_never_escalates():
    env = Env(resolved=False, confirm_threshold_usd=100.0)
    r = await env.orc.start(Question(MEDIUM), UserChoice("flagship"), seed=5)
    assert r.status == "completed" and len(env.rt.repo.tables(r.session_id)) == 1


# --- 单题花费确认 ---------------------------------------------------------------


async def test_cost_confirmation_continue():
    env = Env(confirm_threshold_usd=0.0001)
    r = await env.orc.start(Question(SHORT), UserChoice("flagship"), seed=6)
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "cost"
    assert env.models_called(ANSWER) == []  # 确认前不开始作答
    card = r.checkpoint.card
    assert [o["key"] for o in card["options"]] == ["continue", "plan:budget", "stop"]
    r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "completed"
    assert env.tiers(env.models_called(ANSWER)) == {"flagship"}
    assert record(env, r.session_id)["user_confirmed"] is True


async def test_cost_confirmation_switch_plan():
    env = Env(confirm_threshold_usd=0.0001)
    r = await env.orc.start(Question(SHORT), UserChoice("flagship"), seed=6)
    budget_cost = next(
        o["cost_usd"] for o in r.checkpoint.card["options"] if o["key"] == "plan:budget"
    )
    r = await env.orc.respond(r.session_id, "plan:budget")
    assert r.status == "completed"
    assert env.tiers(env.models_called(ANSWER)) == {"budget"}
    rec = record(env, r.session_id)
    assert rec["plan"] == "budget" and rec["estimated_cost_usd"] == pytest.approx(budget_cost)
    assert rec["extra"]["switched_by_user"]
    table = env.rt.repo.tables(r.session_id)[0]
    assert set(rec["members"]) == set(table["members"].values())


async def test_cost_confirmation_stop():
    env = Env(confirm_threshold_usd=0.0001)
    r = await env.orc.start(Question(SHORT), UserChoice("flagship"), seed=6)
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
        Question(SHORT), UserChoice("custom", ("b1", "b2", "b3", "f1"), "f1"), seed=9
    )
    assert r.status == "completed"  # 正常情况
    env2 = Env(confirm_threshold_usd=100.0)
    env2.fake.queue("b2", *[ErrorKind.SERVER] * 4)
    env2.fake.queue("b3", *[ErrorKind.SERVER] * 4)
    r = await env2.orc.start(
        Question(SHORT), UserChoice("custom", ("b1", "b2", "b3", "f1"), "f1"), seed=9
    )
    assert r.status == "paused" and r.checkpoint.kind == "members"
    r = await env2.orc.respond(r.session_id, "continue")
    assert r.status == "completed" and r.final_answer


async def test_all_members_fail():
    env = Env(confirm_threshold_usd=100.0)
    for m in ("b1", "b2"):
        env.fake.queue(m, *[ErrorKind.SERVER] * 4)
    r = await env.orc.start(Question(SHORT), UserChoice("custom", ("b1", "b2", "f1"), "f1"), seed=9)
    assert r.status == "failed"


async def test_routing_failure_marks_failed():
    env = Env()
    r = await env.orc.start(Question(SHORT), UserChoice("custom", ("b1", "b2", "ghost")), seed=1)
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


async def test_reveal_anonymous_session(env):
    r = await env.orc.start(Question(SHORT), seed=1, anonymous=True)
    view = env.rt.repo.session_view(r.session_id)
    assert view.anonymous and not view.revealed
    assert not any(s.model_id for s in view.seats)
    env.orc.reveal_identities(r.session_id)
    view = env.rt.repo.session_view(r.session_id)
    assert view.revealed and all(s.model_id for s in view.seats)


async def test_not_anonymous_by_default_shows_identity(env):
    r = await env.orc.start(Question(SHORT), seed=1)
    view = env.rt.repo.session_view(r.session_id)
    assert not view.anonymous and view.revealed
    assert all(s.model_id for s in view.seats)


async def test_models_only_see_codes_even_when_not_anonymous(env):
    """匿名开关只管界面：发给模型的内容在两种情况下都只用代号、无任何身份。"""
    terms = set(env.config.models.channels)
    for m in env.config.models.models:
        terms |= {m.id, m.vendor, *m.aliases}
    for anonymous in (False, True):
        env.fake.calls.clear()
        await env.orc.start(Question(MEDIUM), seed=12, anonymous=anonymous)
        peer_steps = [c for c in env.fake.calls if "规划员" not in c.messages[0].content]
        assert peer_steps
        import re

        for c in peer_steps:
            # 组员自己的答案原文会带模型名（Fake 的回复格式），转给别人之前必须遮蔽
            sent = "\n".join(m.content for m in c.messages)
            own = c.model
            found = [
                t
                for t in terms - {own}
                if re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", sent)
            ]
            assert found == [], (anonymous, c.model, found)


# --- 防偷懒与贡献 ---------------------------------------------------------------


async def test_contributions_recorded_when_finished(env):
    r = await env.orc.start(Question(MEDIUM), seed=13)
    rows = env.rt.repo.contributions(r.session_id)
    table = env.rt.repo.tables(r.session_id)[0]
    assert {x["code"] for x in rows} == set(table["members"])
    assert all(x["model_id"] == table["members"][x["code"]] for x in rows)
    kinds = {x["kind"] for x in rows}
    assert {"answered", "adopted", "valid_review", "valid_issue", "issue_accepted"} <= kinds
    history = env.rt.repo.contribution_history()  # 匿名关闭：直接计入历史
    assert {h["model_id"] for h in history} == set(table["members"].values())


async def test_contributions_recorded_when_stopped():
    env = Env(resolved=False, confirm_threshold_usd=100.0)
    r = await env.orc.start(Question(MEDIUM), seed=13, anonymous=True)
    assert r.checkpoint.kind == "escalation"
    r = await env.orc.respond(r.session_id, "stop")
    assert r.status == "stopped" and env.rt.repo.contributions(r.session_id)
    assert env.rt.repo.contribution_history() == []  # 匿名未揭晓：不计入历史
    env.orc.reveal_identities(r.session_id)
    assert env.rt.repo.contribution_history()


async def test_lazy_member_flagged_end_to_end():
    env = Env(confirm_threshold_usd=100.0)
    original = env.reply

    def lazy_b1(model, messages):
        if model == "b1" and "独立完成同一道题" in messages[0].content:
            return "略"
        return original(model, messages)

    env.fake._default = lazy_b1
    r = await env.orc.start(Question(MEDIUM), seed=14)
    assert r.status == "completed"
    table = env.rt.repo.tables(r.session_id)[0]
    if "b1" not in table["members"].values():
        pytest.skip("该种子下 b1 是统筹")
    code = next(c for c, m in table["members"].items() if m == "b1")
    rows = {(x["code"], x["kind"]): x["amount"] for x in env.rt.repo.contributions(r.session_id)}
    assert rows[(code, "lazy")] == 1 and rows[(code, "redo")] == 1
    types = [e.type for _, e in env.events]
    assert "effort_redo" in types and "effort_flagged" in types


# --- 协同模式 -------------------------------------------------------------------

COLLAB = ("decompose", "volunteer", "assign", "work", "cross_review", "rework", "merge", "reveal")


async def test_collab_full_flow(env):
    r = await env.orc.start(Question(MEDIUM), UserChoice(workflow="collab"), seed=21)
    assert r.status == "completed" and r.final_answer.startswith("完整成果")
    assert env.rt.repo.completed_steps(r.session_id, 0) == list(COLLAB)
    assert env.rt.repo.session_row(r.session_id)["workflow"] == "collab"
    rec = record(env, r.session_id)
    assert rec["workflow"] == "collab"
    assert env.models_called(ANSWER) == []  # 没有走讨论模式的作答
    kinds = {x["kind"] for x in env.rt.repo.contributions(r.session_id)}
    assert {"answered", "adopted", "valid_review"} <= kinds


async def test_collab_models_only_see_codes(env):
    terms = set(env.config.models.channels)
    for m in env.config.models.models:
        terms |= {m.id, m.vendor, *m.aliases}
    import re

    for anonymous in (False, True):
        env.fake.calls.clear()
        await env.orc.start(
            Question(MEDIUM),
            UserChoice("flagship", workflow="collab"),
            seed=22,
            anonymous=anonymous,
        )
        for c in env.fake.calls:
            sent = "\n".join(m.content for m in c.messages if m.role != "assistant")
            found = [
                t
                for t in terms - {c.model}
                if re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", sent)
            ]
            assert found == [], (anonymous, c.model, found)


async def test_collab_low_confidence_asks_to_escalate():
    env = Env(resolved=False, confirm_threshold_usd=100.0)
    r = await env.orc.start(Question(MEDIUM), UserChoice(workflow="collab"), seed=23)
    assert r.status == "awaiting_confirmation" and r.checkpoint.kind == "escalation"
    r = await env.orc.respond(r.session_id, "continue")
    # 旗舰档没有 escalate_to：第二张桌子结束后直接完成，仍是协同模式
    assert r.status == "completed"
    tables = env.rt.repo.tables(r.session_id)
    assert [t["status"] for t in tables] == ["done", "done"]
    assert tables[1]["plan"] == "flagship" and tables[1]["pipeline"] == list(COLLAB)


async def test_collab_resume_after_crash():
    env = Env(confirm_threshold_usd=100.0)
    crashed = {"done": False}
    original = env.reply

    def flaky(model, messages):
        if "请你审查分给你的几份成果" in messages[0].content and not crashed["done"]:
            crashed["done"] = True
            raise Crash("进程中断")
        return original(model, messages)

    env.fake._default = flaky
    with pytest.raises(Crash):
        await env.orc.start(Question(MEDIUM), UserChoice(workflow="collab"), seed=24)
    sid = env.rt.repo.list_sessions()[0].id
    assert env.rt.repo.completed_steps(sid, 0) == ["decompose", "volunteer", "assign", "work"]
    work_calls = len(
        [c for c in env.fake.calls if "你负责下面 <your_subtask>" in c.messages[0].content]
    )
    r = await env.orc.resume(sid)
    assert r.status == "completed"
    after = len([c for c in env.fake.calls if "你负责下面 <your_subtask>" in c.messages[0].content])
    assert after == work_calls  # 已完成的子任务没有重做


# --- 花费失控保护 -----------------------------------------------------------------


async def test_overrun_pauses_then_continue_raises_limit():
    env = Env(confirm_threshold_usd=100.0)
    env.fake._reported_cost = 0.05  # 每次调用的实际花费远高于预估
    r = await env.orc.start(Question(SHORT), seed=3)
    assert r.status == "paused" and r.checkpoint.kind == "overrun"
    details = r.checkpoint.card["details"]
    assert details["step"] == "review"  # 第一步照常执行，执行后才知道实际花费
    assert details["next_limit"] > 0
    asked = 1
    while r.status == "paused":
        assert r.checkpoint.kind == "overrun"
        r = await env.orc.respond(r.session_id, "continue")
        asked += 1
    assert r.status == "completed"
    assert asked <= 5  # 每次继续都提高上限，不会每一步都问


async def test_overrun_stop_keeps_partial_results():
    env = Env(confirm_threshold_usd=100.0)
    env.fake._reported_cost = 0.05
    r = await env.orc.start(Question(SHORT), seed=3)
    calls = len(env.fake.calls)
    r = await env.orc.respond(r.session_id, "stop")
    assert r.status == "stopped" and len(env.fake.calls) == calls


async def test_normal_cost_never_triggers_overrun(env):
    r = await env.orc.start(Question(MEDIUM), seed=3)
    while r.status == "awaiting_confirmation" and r.checkpoint.kind == "cost":
        r = await env.orc.respond(r.session_id, "continue")
    assert r.status == "completed"
    kinds = [c.kind for c in env.rt.repo.session_view(r.session_id).checkpoints]
    assert "overrun" not in kinds


async def test_estimate_uses_history_and_shows_upper_bound():
    env = Env(confirm_threshold_usd=0.0)  # 总是弹花费卡片，便于检查文字
    r = await env.orc.start(Question(MEDIUM), seed=4)
    assert r.checkpoint.kind == "cost"
    assert "最多约" in r.checkpoint.card["situation"]
    first = r.checkpoint.card["options"][0]["cost_usd"]
    assert "历史" not in r.checkpoint.card["situation"]
    await env.orc.respond(r.session_id, "continue")
    for seed in range(5, 11):
        r = await env.orc.start(Question(MEDIUM), seed=seed)
        await env.orc.respond(r.session_id, "continue")
    r = await env.orc.start(Question(MEDIUM), seed=11)
    assert "已按本机历史记录校准" in r.checkpoint.card["situation"]
    assert r.checkpoint.card["options"][0]["cost_usd"] != first
