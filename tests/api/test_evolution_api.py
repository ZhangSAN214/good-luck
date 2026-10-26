"""革新例会的 HTTP 接口：概况、开会、导出采纳单；默认关闭时开会返回 409。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env
from ..core.service.test_evolution_service import make
from .test_app import leaks, sse_events


def test_default_off_over_http():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        info = c.get("/api/evolution").json()
        assert info["enabled"] is False and info["cards"] == []
        r = c.post("/api/evolution/meetings")
        assert r.status_code == 409 and "没有开启" in r.json()["detail"]


def test_meet_and_export_over_http(tmp_path):
    env, svc = make(tmp_path)
    with TestClient(create_app(svc)) as c:
        r = c.post("/api/evolution/meetings")
        assert r.status_code == 202
        sid = r.json()["session_id"]
        assert sse_events(c, sid)[-1]["status"] == "completed"
        r = c.post(f"/api/evolution/meetings/{sid}/export")
        assert r.status_code == 200 and r.json()["file"].endswith(".md")
        assert leaks(env, r.text) == []
        assert c.post("/api/evolution/meetings/nope/export").status_code == 404


def test_major_meeting_and_plugins_over_http(tmp_path):
    env, svc = make(tmp_path)
    with TestClient(create_app(svc)) as c:
        r = c.post("/api/evolution/meetings", json={"kind": "major"})
        assert r.status_code == 400 and "主题" in r.json()["detail"]
        assert c.post("/api/evolution/meetings", json={"kind": "x"}).status_code == 422
        r = c.post("/api/evolution/meetings", json={"kind": "major", "theme": "群聊更自然"})
        assert r.status_code == 202 and r.json()["kind"] == "major"
        sid = r.json()["session_id"]
        assert sse_events(c, sid)[-1]["status"] == "completed"
        info = c.get("/api/evolution").json()
        assert info["kinds"]["major"]["top"] == 10 and info["kinds"]["minor"]["top"] == 3
        assert info["meetings"][0]["theme"] == "群聊更自然" and info["themes"] == []
        r = c.post("/api/evolution/meetings/nope/plugins")
        assert r.status_code == 404
