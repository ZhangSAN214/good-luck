"""服务 facade：后台执行、事件订阅、确认、揭晓、恢复；揭晓前所有返回值匿名。"""

from __future__ import annotations

import json
import re

import pytest

from roundtable.core.service import RoundtableService, ServiceError

from ..orchestrator.conftest import MEDIUM, SHORT, Env


def make(**kw) -> tuple[Env, RoundtableService]:
    env = Env(**kw)
    return env, RoundtableService(env.rt)


def identity_terms(env: Env) -> set[str]:
    terms = set(env.config.models.channels)
    for m in env.config.models.models:
        terms |= {m.id, m.vendor, *m.aliases}
    return terms


def leaks(env: Env, data) -> list[str]:
    text = json.dumps(data, ensure_ascii=False, default=str)
    return [
        t
        for t in identity_terms(env)
        if re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", text)
    ]


async def collect(svc: RoundtableService, sid: str) -> list[dict]:
    return [e async for e in svc.events(sid)]


async def test_create_runs_in_background_and_streams_events():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(MEDIUM, seed=2)
    events = await collect(svc, sid)
    types = [e["type"] for e in events]
    assert types[0] == "snapshot" and types[-1] == "state"
    assert events[-1]["status"] == "completed" and not events[-1]["running"]
    assert "routed" in types and "step_started" in types and "call_done" in types
    assert leaks(env, events) == []
    data = svc.session(sid)
    assert data["status"] == "completed" and data["can_reveal"] and not data["revealed"]
    assert leaks(env, data) == []


async def test_events_for_finished_session_end_immediately():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(SHORT, seed=1)
    await svc.wait(sid)
    events = await collect(svc, sid)
    assert [e["type"] for e in events] == ["snapshot"]
    assert events[0]["status"] == "completed"


async def test_checkpoint_flow():
    env, svc = make(confirm_threshold_usd=0.0001)
    sid = svc.create(SHORT, mode="preset", preset="strongest", seed=6)
    events = await collect(svc, sid)
    state = events[-1]
    assert state["status"] == "awaiting_confirmation" and state["checkpoint"]["kind"] == "cost"
    assert svc.session(sid)["pending_checkpoint"]["kind"] == "cost"
    with pytest.raises(ServiceError, match="无效的选项"):
        svc.respond(sid, "maybe")
    svc.respond(sid, "continue")
    events = await collect(svc, sid)
    assert events[-1]["status"] == "completed"
    with pytest.raises(ServiceError) as info:
        svc.respond(sid, "continue")
    assert info.value.status == 409


async def test_reveal_only_after_finish():
    env, svc = make(confirm_threshold_usd=0.0001)
    sid = svc.create(SHORT, mode="preset", preset="strongest", seed=6)
    await svc.wait(sid)
    with pytest.raises(ServiceError) as info:
        svc.reveal_identities(sid)  # 还在等待确认
    assert info.value.status == 409
    svc.respond(sid, "stop")
    await svc.wait(sid)
    data = svc.reveal_identities(sid)
    assert data["revealed"] and not data["can_reveal"]
    assert leaks(env, data)  # 揭晓后可以看到模型


async def test_crash_marks_paused_and_resume_continues():
    env, svc = make(confirm_threshold_usd=100.0)
    original = env.reply
    crashed = {"done": False}

    def flaky(model, messages):
        if "根据审阅意见修订" in messages[0].content and not crashed["done"]:
            crashed["done"] = True
            raise ConnectionError("断网")
        return original(model, messages)

    env.fake._default = flaky
    sid = svc.create(MEDIUM, seed=10)
    events = await collect(svc, sid)
    assert events[-1]["status"] == "paused"
    assert "可以继续" in svc.session(sid)["error"] or svc.session(sid)["error"]
    svc.resume(sid)
    events = await collect(svc, sid)
    assert events[-1]["status"] == "completed"


async def test_invalid_requests():
    env, svc = make()
    for kwargs in (
        {"question": "   "},
        {"question": "x" * 20_001},
        {"question": "q", "mode": "preset"},
        {"question": "q", "mode": "manual"},
        {"question": "q", "mode": "auto", "members": ["b1"]},
    ):
        with pytest.raises(ServiceError):
            svc.create(**kwargs)
    with pytest.raises(ServiceError) as info:
        svc.session("nope")
    assert info.value.status == 404


async def test_routing_error_is_reported_in_session():
    env, svc = make()
    sid = svc.create(SHORT, mode="manual", members=["b1", "ghost"])
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "failed"


async def test_no_models_available(tmp_path):
    from roundtable.core.runtime import Runtime

    svc = RoundtableService(Runtime.build(providers={}, db_path=tmp_path / "x.db"))
    with pytest.raises(ServiceError) as info:
        svc.create(SHORT)
    assert info.value.status == 503


async def test_status_and_budget():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(SHORT, seed=1)
    await svc.wait(sid)
    status = svc.status()
    assert status["plans"]["simple"] == "单人快答"
    assert set(status["presets"]) == {"saver", "balanced", "strongest"}
    assert any(m["available"] for m in status["models"])
    budget = status["budget"]
    assert budget["month"]["limit_usd"] == 20.0 and budget["day"]["limit_usd"] == 3.0
    assert budget["by_channel"]["c"]["calls"] >= 1 and budget["total_usd"] > 0
    assert svc.sessions()[0]["id"] == sid


async def test_unrouted_session_resumes_with_routing():
    """进程在路由之前中断：恢复时从路由开始，而不是误判为已完成。"""
    env, svc = make(confirm_threshold_usd=100.0)
    from roundtable.core.routing import Question, UserChoice

    sid = svc.orc.open(Question(SHORT), UserChoice("manual", members=("b1",)), seed=3)
    svc.resume(sid)
    events = await collect(svc, sid)
    assert events[-1]["status"] == "completed"
    assert env.models_called("这是一道简单题") == ["b1"]  # 用到了保存的手动选择


async def test_session_tables_show_plan_and_progress_without_models():
    env, svc = make(resolved=False, confirm_threshold_usd=100.0)
    sid = svc.create(MEDIUM, seed=3)
    await svc.wait(sid)
    tables = svc.session(sid)["tables"]
    assert [t["table_no"] for t in tables] == [0, 1]
    first, second = tables
    assert first["plan"] == "medium" and first["plan_label"] == "小圆桌"
    assert first["steps_done"] == first["pipeline"]
    assert first["codes"] == ["甲", "乙"] and first["estimate_usd"] > 0
    assert second["plan"] == "hard" and second["escalation_reason"]
    assert leaks(env, tables) == []
