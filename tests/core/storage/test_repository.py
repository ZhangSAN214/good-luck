from __future__ import annotations

import json
import sqlite3

import pytest

from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import ErrorKind, FakeProvider
from roundtable.core.routing import Question, UserChoice, route_question
from roundtable.core.storage import NotFound

from .conftest import CONFIG, MSG

# --- 会话 ---------------------------------------------------------------------


def test_session_lifecycle(repo):
    sid = repo.create_session("题目", seed=7, tier="flagship", attachments=["image"])
    row = repo.session_row(sid)
    assert (row["question"], row["seed"], row["mode"], row["choice"]) == (
        "题目",
        7,
        "flagship",
        {"tier": "flagship"},
    )
    assert row["anonymous"] == 1 and row["workflow"] == "discussion"  # 存储层默认匿名
    assert row["attachments"] == ["image"] and row["status"] == "created"
    repo.set_status(sid, "running")
    repo.set_status(sid, "failed", error="出错")
    assert repo.session_row(sid)["status"] == "failed"
    with pytest.raises(ValueError):
        repo.set_status(sid, "exploded")
    assert not repo.is_revealed(sid)
    repo.mark_revealed(sid)
    first = repo.session_row(sid)["revealed_at"]
    repo.mark_revealed(sid)  # 重复揭晓不改时间
    assert repo.is_revealed(sid) and repo.session_row(sid)["revealed_at"] == first


def test_unknown_session(repo):
    with pytest.raises(NotFound):
        repo.set_status("nope", "running")
    with pytest.raises(NotFound):
        repo.session_view("nope")


# --- 路由记录 -----------------------------------------------------------------


async def make_decision(router, text="1+1=?", choice=None):
    q = Question(text)
    d = await route_question(
        q, choice, config=CONFIG, router=router, prompts=PromptLibrary(), seed=3
    )
    return q, d


async def test_routing_record_roundtrip_and_update(repo, router):
    q, d = await make_decision(router, choice=UserChoice("flagship"))
    sid = repo.create_session(q.text, seed=d.seed)
    record = d.record(q)
    repo.save_routing(sid, record)
    stored = repo.routing_record(sid)
    assert stored["plan"] == "flagship" and stored["members"] == list(d.lineup.members)
    assert stored["actual_cost_usd"] is None

    repo.save_routing(sid, record.with_outcome(actual_cost_usd=0.02))  # 结束后更新
    assert repo.routing_record(sid)["actual_cost_usd"] == 0.02
    assert len(repo.routing_records()) == 1


def test_non_anonymous_session_always_shows_identity(repo):
    sid = repo.create_session("题目", seed=1, anonymous=False)
    repo.add_seats(sid, 0, {"甲": "m1", "乙": "m2"}, coordinator="c1")
    assert repo.is_revealed(sid)
    view = repo.session_view(sid, scrub=lambda text, q: "[遮蔽]")
    assert not view.anonymous and view.revealed
    assert {s.model_id for s in view.seats} == {"m1", "m2", "c1"}
    summary = repo.list_sessions()[0]
    assert summary.revealed and not summary.anonymous and summary.workflow == "discussion"


def test_old_routing_record_still_loads(repo):
    """迁移前的路由记录（含 mode / preset 等旧字段）仍能读取。"""
    from roundtable.core.routing import RoutingRecord

    old = {
        "seed": 1,
        "question_chars": 3,
        "attachments": [],
        "mode": "preset",
        "preset": "saver",
        "difficulty": "medium",
        "difficulty_source": "skipped",
        "task_type": None,
        "require_tags": [],
        "rules_matched": [],
        "assessment_reason": "",
        "expected_answer_tokens": None,
        "planner_model": None,
        "planner_cost_usd": 0.0,
        "planner_error": None,
        "plan": "medium",
        "members": ["m1", "m2"],
        "coordinator": "c1",
        "estimated_cost_usd": 0.01,
        "needs_confirmation": False,
        "options": {"medium": 0.01},
    }
    record = RoutingRecord.from_dict(old)
    assert record.plan == "medium" and record.absent == ()
    assert record.difficulty_source == "default"


# --- 座位与轮换 ---------------------------------------------------------------


def test_seats_and_recent_coordinators(repo):
    a = repo.create_session("q1", seed=1)
    repo.add_seats(a, 0, {"甲": "m1", "乙": "m2"}, coordinator="c1")
    b = repo.create_session("q2", seed=2)
    repo.add_seats(b, 0, {"甲": "m3", "乙": "m1"}, coordinator="c2")
    repo.add_seats(b, 1, {"甲": "m4", "乙": "m5"}, coordinator="c1")  # 升级后的第二桌
    assert repo.recent_coordinators() == ["c1", "c2"]
    assert [s["code"] for s in repo.seats(b, 0)] == ["甲", "乙", None]
    assert len(repo.seats(b)) == 6


def test_duplicate_code_rejected_atomically(repo):
    sid = repo.create_session("q", seed=1)
    repo.add_seats(sid, 0, {"甲": "m1"})
    with pytest.raises(sqlite3.IntegrityError):
        repo.add_seats(sid, 0, {"乙": "m2", "甲": "m3"})
    assert [s["code"] for s in repo.seats(sid)] == ["甲"]  # 整批回滚


# --- 调用 ---------------------------------------------------------------------


async def test_record_successful_call_with_failover(repo):
    from roundtable.core.providers import ChannelRouter

    google = FakeProvider("google")
    google.queue("gemini-3.8-flash", ErrorKind.RATE_LIMIT)
    providers = {n: FakeProvider(n) for n in CONFIG.models.channels} | {"google": google}
    router = ChannelRouter(CONFIG.models, providers)
    completion = await router.complete("gemini-3.8-flash", MSG)
    prompt = PromptLibrary().render("answer", "v1", code="组员甲", question="题目")

    sid = repo.create_session("题目", seed=1)
    call_id = repo.record_call(
        sid,
        step="answer",
        role="member",
        model_id="gemini-3.8-flash",
        messages=MSG,
        table_no=0,
        code="甲",
        prompt=prompt,
        completion=completion,
    )
    row = repo.conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    assert (row["channel"], row["prompt_role"], row["prompt_version"]) == (
        "openrouter",
        "answer",
        "v1",
    )
    assert row["prompt_sha256"] == prompt.sha256
    assert json.loads(row["input"])[1] == {"role": "user", "content": "题目"}
    attempts = repo.conn.execute(
        "SELECT channel, ok, error FROM call_attempts WHERE call_id = ? ORDER BY seq", (call_id,)
    ).fetchall()
    assert [tuple(a) for a in attempts] == [("google", 0, "rate_limit"), ("openrouter", 1, None)]


async def test_record_failed_call(repo):
    from roundtable.core.providers import AllChannelsFailed, ChannelRouter

    providers = {n: FakeProvider(n, default=ErrorKind.SERVER) for n in CONFIG.models.channels}
    router = ChannelRouter(
        CONFIG.models,
        providers,
        policy=CONFIG.roundtable.request.model_copy(update={"failover_rounds": 1}),
    )
    sid = repo.create_session("q", seed=1)
    with pytest.raises(AllChannelsFailed) as info:
        await router.complete("gemini-3.8-flash", MSG)
    call_id = repo.record_call(
        sid,
        step="answer",
        role="member",
        model_id="gemini-3.8-flash",
        messages=MSG,
        table_no=0,
        code="甲",
        failure=info.value,
    )
    row = repo.conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    assert row["error"] and row["cost_usd"] == 0 and row["output"] is None
    n = repo.conn.execute(
        "SELECT COUNT(*) FROM call_attempts WHERE call_id = ?", (call_id,)
    ).fetchone()[0]
    assert n == 2


def test_record_call_needs_exactly_one_outcome(repo):
    sid = repo.create_session("q", seed=1)
    with pytest.raises(ValueError):
        repo.record_call(sid, step="answer", role="member", model_id="m", messages=MSG)


async def test_record_planner_calls(repo, router):
    q, d = await make_decision(router, "求函数 f(x)=x^3-3x 在 [-2,2] 上的最值，并说明理由。")
    planner = d.assessment.planner
    assert planner is not None  # 自动模式且规则判断不出 → 调用了规划员
    sid = repo.create_session(q.text, seed=d.seed)
    ids = repo.record_planner(sid, planner)
    assert len(ids) == planner.calls
    row = repo.conn.execute(
        "SELECT role, step, prompt_role FROM calls WHERE id = ?", (ids[0],)
    ).fetchone()
    assert tuple(row) == ("planner", "plan", "planner")
    assert repo.session_cost(sid) == pytest.approx(planner.cost_usd)


# --- 产出、进度、确认点 ---------------------------------------------------------


def test_outputs_text_and_json(repo):
    sid = repo.create_session("q", seed=1)
    repo.save_output(sid, table_no=0, step="answer", kind="answer", code="甲", content="答案")
    repo.save_output(
        sid, table_no=0, step="review", kind="review", code="乙", content={"reviews": []}
    )
    assert [o["content"] for o in repo.outputs(sid)] == ["答案", '{"reviews": []}']
    assert len(repo.outputs(sid, kind="review")) == 1


def test_step_progress(repo):
    sid = repo.create_session("q", seed=1)
    repo.mark_step_done(sid, 0, "answer")
    repo.mark_step_done(sid, 0, "review")
    repo.mark_step_done(sid, 0, "answer")  # 重复标记无副作用
    repo.mark_step_done(sid, 1, "answer")
    assert repo.completed_steps(sid, 0) == ["answer", "review"]
    assert repo.completed_steps(sid, 1) == ["answer"]


def test_checkpoints(repo):
    sid = repo.create_session("q", seed=1)
    card = {"现状": "预计 $0.35", "选项": ["继续", "停止"], "代价": {"继续": 0.35}, "推荐": "继续"}
    cid = repo.create_checkpoint(sid, "cost", card)
    assert repo.pending_checkpoint(sid).card == card
    with pytest.raises(ValueError):
        repo.create_checkpoint(sid, "cost", card)  # 同时只能有一个待回复
    repo.answer_checkpoint(cid, "continue", note="可以")
    assert repo.pending_checkpoint(sid) is None
    with pytest.raises(ValueError):
        repo.answer_checkpoint(cid, "stop")
    with pytest.raises(NotFound):
        repo.answer_checkpoint(999, "stop")


# --- 用量 ---------------------------------------------------------------------


async def test_usage_totals_and_by_channel(repo):
    from roundtable.core.providers import ChannelRouter

    google = FakeProvider("google", reported_cost_usd=0.01)
    google.queue("gemini-3.8-flash", ErrorKind.QUOTA)
    providers = {n: FakeProvider(n, reported_cost_usd=0.02) for n in CONFIG.models.channels} | {
        "google": google
    }
    router = ChannelRouter(CONFIG.models, providers)
    a, b = repo.create_session("a", seed=1), repo.create_session("b", seed=2)
    for sid in (a, b):
        c = await router.complete("gemini-3.8-flash", MSG)
        repo.record_call(
            sid, step="answer", role="member", model_id=c.model_id, messages=MSG, completion=c
        )
    # 第一次：google 额度用尽 → openrouter（0.02）；第二次：google 冷却中 → openrouter（0.02）
    assert repo.total_spent() == pytest.approx(0.04)
    assert repo.session_cost(a) == pytest.approx(0.02)
    usage = repo.spent_by_channel()
    assert usage["openrouter"].calls == 2 and usage["openrouter"].cost_usd == pytest.approx(0.04)
    assert usage["google"].failed_attempts == 1 and usage["google"].calls == 0


def test_list_sessions(repo):
    a = repo.create_session("第一题", seed=1)
    b = repo.create_session("第二题", seed=2)
    repo.mark_revealed(a)
    listed = repo.list_sessions()
    assert [s.id for s in listed] == [b, a]
    assert listed[1].revealed and not listed[0].revealed


def test_table_plan_keeps_seat_order(repo):
    sid = repo.create_session("q", seed=1)
    members = {"甲": "m3", "乙": "m1", "丙": "m2"}
    repo.create_table(
        sid,
        0,
        plan="medium",
        pipeline=["answer"],
        members=members,
        coordinator="c",
        escalate_to=None,
        estimate={"total": 0.1, "steps": {"answer": 0.1}},
    )
    [table] = repo.tables(sid)
    assert list(table["members"]) == ["甲", "乙", "丙"]  # 不能被按键排序打乱
    repo.replace_table(
        sid,
        0,
        plan="simple",
        pipeline=["answer"],
        members={"甲": "m9"},
        coordinator=None,
        escalate_to=None,
        estimate={"total": 0.0, "steps": {}},
        status="approved",
    )
    assert repo.tables(sid)[0]["members"] == {"甲": "m9"}
    repo.set_table_status(sid, 0, "done")
    assert repo.tables(sid)[0]["status"] == "done"


# --- 贡献 ---------------------------------------------------------------------


def test_contributions_replace_and_history(repo):
    a = repo.create_session("q1", seed=1, anonymous=False)
    repo.replace_contributions(a, 0, [("甲", "m1", "adopted", 2), ("乙", "m2", "lazy", 1)])
    repo.replace_contributions(a, 0, [("甲", "m1", "adopted", 3), ("乙", "m2", "redo", 0)])
    assert repo.contributions(a) == [
        {"table_no": 0, "code": "甲", "model_id": "m1", "kind": "adopted", "amount": 3}
    ]  # 整桌重写；数量为 0 的不存
    b = repo.create_session("q2", seed=2, anonymous=True)  # 匿名且未揭晓：不计入历史
    repo.replace_contributions(b, 0, [("甲", "m1", "adopted", 5)])
    assert repo.contribution_history() == [
        {"model_id": "m1", "kind": "adopted", "amount": 3, "sessions": 1}
    ]
    repo.mark_revealed(b)
    assert repo.contribution_history() == [
        {"model_id": "m1", "kind": "adopted", "amount": 8, "sessions": 2}
    ]


async def test_table_cost_and_call_samples(repo):
    from roundtable.core.providers import ChannelRouter

    providers = {n: FakeProvider(n, reported_cost_usd=0.01) for n in CONFIG.models.channels}
    router = ChannelRouter(CONFIG.models, providers)
    sids = [repo.create_session(f"q{i}", seed=i) for i in range(3)]
    for sid in sids:
        for table_no in (0, 1):
            completion = await router.complete("gemini-3.8-flash", MSG)
            repo.record_call(
                sid,
                step="answer",
                role="member",
                model_id="gemini-3.8-flash",
                messages=MSG,
                table_no=table_no,
                code="甲",
                completion=completion,
            )
    assert repo.table_cost(sids[0], 0) == pytest.approx(0.01)
    assert repo.session_cost(sids[0]) == pytest.approx(0.02)
    rows = repo.call_samples()
    assert len(rows) == 6 and {r["step"] for r in rows} == {"answer"}
    assert set(rows[0]) >= {"model_id", "input_tokens", "output_tokens", "code", "error"}
    assert len(repo.call_samples(sessions=1)) == 2  # 只看最近的若干场


async def test_call_records_finish_reason_and_reasoning_tokens(repo):
    from roundtable.core.providers import ChannelRouter, RawCompletion

    fake = FakeProvider("google")
    fake.queue(
        "gemini-3.8-flash",
        RawCompletion(
            "短", output_tokens=4000, truncated=True, finish_reason="length", reasoning_tokens=3900
        ),
    )
    providers = {n: FakeProvider(n) for n in CONFIG.models.channels} | {"google": fake}
    router = ChannelRouter(CONFIG.models, providers, policy=CONFIG.roundtable.request)
    completion = await router.complete("gemini-3.8-flash", MSG)
    sid = repo.create_session("q", seed=1)
    call_id = repo.record_call(
        sid, step="answer", role="member", model_id="gemini-3.8-flash", messages=MSG,
        table_no=0, code="甲", completion=completion,
    )  # fmt: skip
    row = repo.conn.execute("SELECT * FROM calls WHERE id = ?", (call_id,)).fetchone()
    assert (row["finish_reason"], row["reasoning_tokens"]) == ("length", 3900)
