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


def test_collab_over_http():
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        r = c.post("/api/sessions", json={"question": MEDIUM, "workflow": "collab"})
        sid = r.json()["session_id"]
        assert sse_events(c, sid)[-1]["status"] == "completed"
        session = c.get(f"/api/sessions/{sid}").json()
        assert session["workflow"] == "collab"
        assert any(o["kind"] == "merge" for o in session["outputs"])
        bad = c.post("/api/sessions", json={"question": MEDIUM, "workflow": "debate"})
        assert bad.status_code == 400


def test_contributions_endpoint():
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        assert c.get("/api/contributions").json() == []
        sid = c.post("/api/sessions", json={"question": MEDIUM, "seed": 2}).json()["session_id"]
        sse_events(c, sid)
        history = c.get("/api/contributions").json()
        assert history and all("counts" in h and h["sessions"] == 1 for h in history)
        session = c.get(f"/api/sessions/{sid}").json()
        assert session["contributions"] and all(r["model_id"] for r in session["contributions"])


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


def test_upload_then_ask_with_attachments(caplog):
    import logging

    from ..core.attachments.samples import PNG

    marker = "独一无二的讲义内容-XYZZY"
    env, c = client(confirm_threshold_usd=100.0)
    with c, caplog.at_level(logging.DEBUG):
        r = c.post("/api/uploads", params={"name": "../../讲义.txt"}, content=marker.encode())
        assert r.status_code == 201
        notes = r.json()
        assert notes["name"] == "讲义.txt" and notes["kind"] == "text"
        assert notes["status"] == "ready" and "storage_key" not in notes
        image = c.post("/api/uploads", params={"name": "g.png"}, content=PNG).json()
        assert image["status"] == "pending"

        bad = c.post("/api/uploads", params={"name": "x.png"}, content=b"not an image")
        assert bad.status_code == 400 and "不符" in bad.json()["detail"]

        body = {"question": MEDIUM, "seed": 3, "attachments": [notes["id"], image["id"]]}
        sid = c.post("/api/sessions", json=body).json()["session_id"]
        events = sse_events(c, sid)
        assert events[-1]["status"] == "completed"
        detail = c.get(f"/api/sessions/{sid}").json()
        names = [(a["name"], a["status"], a["text_source"]) for a in detail["attachments"]]
        assert names == [("讲义.txt", "ready", "extract"), ("g.png", "ready", "vision")]
        assert marker not in json.dumps(detail, ensure_ascii=False)  # 详情不含文件内容

        again = c.post("/api/sessions", json=body)
        assert again.status_code == 400 and "已用于其他讨论" in again.json()["detail"]
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert marker not in logged  # 上传内容不进日志


def test_upload_size_limit():
    env, c = client()
    limit = int(env.config.roundtable.uploads.max_file_mb * 1024 * 1024)
    with c:
        r = c.post("/api/uploads", params={"name": "big.txt"}, content=b"a" * (limit + 1))
        assert r.status_code == 413
        status = c.get("/api/status").json()
        assert status["uploads"]["max_files"] >= 1 and "pdf" in status["uploads"]["types"]
