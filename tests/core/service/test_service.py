"""服务 facade：后台执行、事件订阅、确认、揭晓、恢复；揭晓前所有返回值匿名。"""

from __future__ import annotations

import json
import re

import pytest

from roundtable.core.config import load_config
from roundtable.core.service import RoundtableService, ServiceError

from ..orchestrator.conftest import MEDIUM, SHORT, Env

TAROT = set(load_config().personas.codes)


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
    sid = svc.create(MEDIUM, seed=2, anonymous=True)
    events = await collect(svc, sid)
    types = [e["type"] for e in events]
    assert types[0] == "snapshot" and types[-1] == "state"
    assert events[-1]["status"] == "completed" and not events[-1]["running"]
    assert "routed" in types and "step_started" in types and "call_done" in types
    assert leaks(env, events) == []
    data = svc.session(sid)
    assert data["status"] == "completed" and data["can_reveal"] and not data["revealed"]
    assert data["anonymous"] and leaks(env, data) == []


async def test_not_anonymous_by_default():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(MEDIUM, seed=2)
    events = await collect(svc, sid)
    assert leaks(env, events) == []  # 事件流本身只含代号
    data = svc.session(sid)
    assert not data["anonymous"] and data["revealed"] and not data["can_reveal"]
    assert leaks(env, data)  # 匿名关闭：会话详情直接显示模型
    assert all(s["model_id"] for s in data["seats"])
    with pytest.raises(ServiceError, match="没有开启匿名") as info:
        svc.reveal_identities(sid)
    assert info.value.status == 409


async def test_events_for_finished_session_end_immediately():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(SHORT, seed=1)
    await svc.wait(sid)
    events = await collect(svc, sid)
    assert [e["type"] for e in events] == ["snapshot"]
    assert events[0]["status"] == "completed"


async def test_checkpoint_flow():
    env, svc = make(confirm_threshold_usd=0.0001)
    sid = svc.create(SHORT, tier="flagship", seed=6)
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
    sid = svc.create(SHORT, tier="flagship", seed=6, anonymous=True)
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
        {"question": "q", "tier": "giant"},
        {"question": "q", "tier": "custom"},
        {"question": "q", "tier": "budget", "models": ["b1"]},
        {"question": "q", "tier": "custom", "models": ["b1", "b2"], "coordinator": "f1"},
    ):
        with pytest.raises(ServiceError):
            svc.create(**kwargs)
    with pytest.raises(ServiceError) as info:
        svc.session("nope")
    assert info.value.status == 404


async def test_routing_error_is_reported_in_session():
    env, svc = make()
    sid = svc.create(SHORT, tier="custom", models=["b1", "b2", "ghost"])
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
    assert status["plans"] == {"budget": "节电模式", "flagship": "全力模式"}
    assert status["default_plan"] == "budget" and status["custom_label"] == "自选"
    assert status["min_members"] == 2
    assert any(m["available"] for m in status["models"])
    budget = status["budget"]
    assert budget["month"]["limit_usd"] == 20.0 and budget["day"]["limit_usd"] == 3.0
    assert budget["by_channel"]["c"]["calls"] >= 1 and budget["total_usd"] > 0
    assert svc.sessions()[0]["id"] == sid


async def test_unrouted_session_resumes_with_routing():
    """进程在路由之前中断：恢复时从路由开始，而不是误判为已完成。"""
    env, svc = make(confirm_threshold_usd=100.0)
    from roundtable.core.routing import Question, UserChoice

    sid = svc.orc.open(Question(SHORT), UserChoice("custom", ("b1", "b2", "f1"), "f1"), seed=3)
    svc.resume(sid)
    events = await collect(svc, sid)
    assert events[-1]["status"] == "completed"
    assert sorted(env.models_called("独立完成同一道题")) == ["b1", "b2"]  # 用到了保存的自选


async def test_old_session_without_routing_fails_clearly():
    """v2 之前创建、还没路由就中断的会话：恢复时说明无法继续，而不是崩溃。"""
    env, svc = make()
    sid = env.rt.repo.create_session(
        SHORT, seed=1, anonymous=False, choice={"mode": "auto", "preset": None}
    )
    svc.resume(sid)
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "failed" and "旧版本" in data["error"]


async def test_session_tables_show_plan_and_progress_without_models():
    env, svc = make(resolved=False, confirm_threshold_usd=100.0)
    sid = svc.create(MEDIUM, seed=3, anonymous=True)
    await svc.wait(sid)
    svc.respond(sid, "continue")  # 升级总是先询问
    await svc.wait(sid)
    tables = svc.session(sid)["tables"]
    assert [t["table_no"] for t in tables] == [0, 1]
    first, second = tables
    assert first["plan"] == "budget" and first["plan_label"] == "节电模式"
    assert first["steps_done"] == first["pipeline"]
    assert len(first["codes"]) == 2 and set(first["codes"]) <= TAROT and first["estimate_usd"] > 0
    assert second["plan"] == "flagship" and second["escalation_reason"]
    assert len(second["codes"]) == 4 and set(second["codes"]) <= TAROT
    assert leaks(env, tables) == []


async def test_custom_plan_label():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(SHORT, tier="custom", models=["b1", "b2", "f1"], seed=1)
    await svc.wait(sid)
    assert svc.session(sid)["tables"][0]["plan_label"] == "自选"


async def test_session_contributions_and_history():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(MEDIUM, seed=2, anonymous=True)
    await svc.wait(sid)
    data = svc.session(sid)
    rows = data["contributions"]
    assert len({r["code"] for r in rows}) == 2 and {r["code"] for r in rows} <= TAROT
    assert all(r["model_id"] is None for r in rows)  # 匿名未揭晓：不含模型
    assert all(r["counts"]["answered"] == 1 for r in rows)
    assert leaks(env, rows) == []
    assert svc.contributions() == []  # 未揭晓：不计入历史
    revealed = svc.reveal_identities(sid)
    assert all(r["model_id"] for r in revealed["contributions"])
    history = svc.contributions()
    assert {h["model_id"] for h in history} == {r["model_id"] for r in revealed["contributions"]}
    assert all(h["sessions"] == 1 and h["counts"]["answered"] == 1 for h in history)


async def test_collab_session_over_service():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(MEDIUM, workflow="collab", seed=5, anonymous=True)
    events = await collect(svc, sid)
    assert events[-1]["status"] == "completed"
    data = svc.session(sid)
    assert data["workflow"] == "collab"
    kinds = {o["kind"] for o in data["outputs"]}
    assert {
        "subtasks",
        "volunteer",
        "assignment",
        "work",
        "cross_review",
        "rework",
        "merge",
    } <= kinds
    assert data["tables"][0]["pipeline"][0] == "decompose"
    assert leaks(env, data) == [] and leaks(env, events) == []
    assert svc.status()["workflows"] == {"discussion": "讨论模式", "collab": "协同模式"}
    with pytest.raises(ServiceError, match="未知的模式"):
        svc.create(MEDIUM, workflow="debate")


# --- 称呼：匿名 = 塔罗牌，匿名关闭 = 昵称·模式 ---------------------------------------


def nickname_terms(env: Env) -> set[str]:
    return set(env.config.personas.nicknames.values())


async def test_anonymous_session_never_shows_nicknames_models_or_vendors():
    """匿名开启：事件、会话详情、贡献、发给模型的每条消息里都没有昵称、模型 id、厂商名。"""
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(MEDIUM, seed=4, anonymous=True)
    events = await collect(svc, sid)
    data = svc.session(sid)
    terms = identity_terms(env) | nickname_terms(env)

    def found(text: str, allowed=()) -> list[str]:
        return [
            t
            for t in terms - set(allowed)
            if re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", text)
        ]

    for name, payload in (
        ("events", events),
        ("session", data),
        ("contributions", data["contributions"]),
        ("history", svc.contributions()),
    ):
        assert found(json.dumps(payload, ensure_ascii=False, default=str)) == [], name
    assert set(data["tables"][0]["codes"]) <= TAROT
    assert all("label" not in s for s in data["seats"])  # 统筹没有昵称称呼
    sent = [c for c in env.fake.calls if "规划员" not in c.messages[0].content]
    assert sent
    for call in sent:
        text = "\n".join(m.content for m in call.messages)
        assert found(text, allowed={call.model}) == [], (
            call.model
        )  # 自己的答案里有自己的 id（Fake 的格式）
        assert not any(n in text for n in nickname_terms(env))
    # 揭晓后才能看到模型
    svc.reveal_identities(sid)
    assert any(s["model_id"] for s in svc.session(sid)["seats"])


async def test_non_anonymous_members_are_named_by_nickname_and_mode():
    env, svc = make(confirm_threshold_usd=100.0)
    sid = svc.create(MEDIUM, seed=2, tier="budget")
    await svc.wait(sid)
    data = svc.session(sid)
    codes = data["tables"][0]["codes"]
    assert all(c.endswith("·节电") and c.split("·")[0] in nickname_terms(env) for c in codes)
    coordinator = next(s for s in data["seats"] if s["role"] == "coordinator")
    assert coordinator["label"].endswith("·节电（统筹）")
    assert coordinator["label"].split("·")[0] in nickname_terms(env)
    # 成员之间互相称呼也用昵称：评审者收到的答案标签是昵称，不是模型 id
    review_calls = [c for c in env.fake.calls if "审阅每一份答案" in c.messages[0].content]
    assert review_calls
    text = "\n".join(m.content for m in review_calls[0].messages)
    assert any(c in text for c in codes)
    assert "[已隐去]" not in text  # 匿名关闭：不做任何身份遮蔽


def test_status_carries_rice_ratios_and_mode_names():
    env, svc = make()
    status = svc.status()
    assert status["rice"] == {
        "grain_name": "粒",
        "spoon_name": "勺",
        "bowl_name": "碗",
        "grain_tokens": 1000,
        "spoon_grains": 100,
        "bowl_spoons": 30,
    }
    assert status["plans"] == {"budget": "节电模式", "flagship": "全力模式"}
