"""HTTP 接口与 SSE：用 Fake 模型跑完整流程；揭晓前所有响应匿名、不含 key。"""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import MEDIUM, SHORT, Env

FAKE_KEY = "sk-or-v1-" + "a" * 48


def client(**kw) -> tuple[Env, TestClient]:
    env = Env(**kw)
    return env, TestClient(create_app(RoundtableService(env.rt)))


def sse_events(c: TestClient, sid: str) -> list[dict]:
    with c.stream("GET", f"/api/sessions/{sid}/events") as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        event = json.loads(lines["data"])
        assert event["type"] == lines["event"]
        events.append(event)
    return events


def leaks(env: Env, text: str) -> list[str]:
    terms = set(env.config.models.channels)
    for m in env.config.models.models:
        terms |= {m.id, m.vendor, *m.aliases}
    return [t for t in terms if re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", text)]


def test_full_flow_over_http():
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        r = c.post("/api/sessions", json={"question": MEDIUM, "seed": 2, "anonymous": True})
        assert r.status_code == 202
        sid = r.json()["session_id"]
        events = sse_events(c, sid)
        assert events[0]["type"] == "snapshot" and events[-1]["status"] == "completed"
        session = c.get(f"/api/sessions/{sid}")
        assert session.status_code == 200 and session.json()["can_reveal"]
        listing = c.get("/api/sessions")
        everything = json.dumps(events, ensure_ascii=False) + session.text + listing.text
        assert leaks(env, everything) == []

        revealed = c.post(f"/api/sessions/{sid}/reveal")
        assert revealed.status_code == 200 and revealed.json()["revealed"]
        assert leaks(env, revealed.text)


def test_not_anonymous_session_over_http():
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        sid = c.post("/api/sessions", json={"question": SHORT}).json()["session_id"]
        events = sse_events(c, sid)
        assert leaks(env, json.dumps(events, ensure_ascii=False)) == []  # 事件只含代号
        session = c.get(f"/api/sessions/{sid}").json()
        assert not session["anonymous"] and session["revealed"] and not session["can_reveal"]
        assert leaks(env, json.dumps(session, ensure_ascii=False))
        assert c.post(f"/api/sessions/{sid}/reveal").status_code == 409


def test_checkpoint_over_http():
    env, c = client(confirm_threshold_usd=0.0001)
    with c:
        sid = c.post(
            "/api/sessions", json={"question": SHORT, "tier": "flagship", "anonymous": True}
        ).json()["session_id"]
        state = sse_events(c, sid)[-1]
        assert state["checkpoint"]["kind"] == "cost"
        keys = [o["key"] for o in state["checkpoint"]["card"]["options"]]
        assert "plan:budget" in keys
        assert c.post(f"/api/sessions/{sid}/reveal").status_code == 409
        assert c.post(f"/api/sessions/{sid}/respond", json={"response": "bogus"}).status_code == 400
        assert (
            c.post(f"/api/sessions/{sid}/respond", json={"response": "plan:budget"}).status_code
            == 202
        )
        assert sse_events(c, sid)[-1]["status"] == "completed"


def test_errors():
    env, c = client()
    with c:
        assert c.get("/api/sessions/nope").status_code == 404
        assert c.get("/api/sessions/nope/events").status_code == 404
        assert c.post("/api/sessions", json={"question": ""}).status_code == 422
        r = c.post("/api/sessions", json={"question": "q", "tier": "custom"})
        assert r.status_code == 400 and "自选" in r.json()["detail"]
        r = c.post("/api/sessions", json={"question": "q", "tier": "giant"})
        assert r.status_code == 400 and "档位" in r.json()["detail"]
        # 旧版本的字段不再接受
        assert c.post("/api/sessions", json={"question": "q", "mode": "auto"}).status_code == 422
        assert c.post("/api/sessions/nope/resume").status_code == 404


def test_status_and_budget_endpoints():
    env, c = client()
    with c:
        status = c.get("/api/status").json()
        assert status["channel_mode"] == "auto" and status["code_prefix"] == "组员"
        assert c.get("/api/budget").json()["month"]["limit_usd"] == 20.0


def test_responses_never_contain_keys(tmp_path):
    """用真实格式的 key 构建运行时（渠道用 Fake 替换），所有响应中都不得出现 key。"""
    from roundtable.core.providers import FakeProvider, KeyRing, build_providers
    from roundtable.core.runtime import Runtime

    env = Env(confirm_threshold_usd=100.0)
    keys = KeyRing.from_env(["OPENROUTER_API_KEY"], environ={"OPENROUTER_API_KEY": FAKE_KEY})
    _ = build_providers  # key 已登记到脱敏表
    rt = Runtime.build(
        config=env.config,
        providers={"c": FakeProvider("c", default=env.reply)},
        db_path=tmp_path / "k.db",
    )
    c = TestClient(create_app(RoundtableService(rt)))
    with c:
        sid = c.post("/api/sessions", json={"question": MEDIUM, "anonymous": True}).json()[
            "session_id"
        ]
        events = sse_events(c, sid)
        texts = [
            json.dumps(events),
            c.get(f"/api/sessions/{sid}").text,
            c.get("/api/status").text,
            c.post(f"/api/sessions/{sid}/reveal").text,
            c.get("/api/budget").text,
        ]
        for text in texts:
            assert FAKE_KEY not in text
    assert keys.has("OPENROUTER_API_KEY")


def test_openapi_available():
    env, c = client()
    with c:
        assert "/api/sessions" in c.get("/openapi.json").json()["paths"]


def test_frontend_is_served():
    _, c = client()
    with c:
        index = c.get("/")
        assert index.status_code == 200 and "圆桌" in index.text
        for path in ("/css/app.css", "/js/app.js", "/js/api.js", "/js/view.js"):
            assert c.get(path).status_code == 200, path
        # API 路由不被静态文件覆盖
        assert c.get("/api/status").status_code == 200
