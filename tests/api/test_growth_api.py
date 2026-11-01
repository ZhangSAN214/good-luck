"""成员成长接口：时间线、马上反思、撤回一条、清空时间线；不认识的成员和改动报 400。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.member.growth.common import NOW, WEEK, fake_growth, setup
from ..core.orchestrator.conftest import Env


def test_growth_endpoints():
    env = Env()
    setup(env)
    fake_growth(env)
    svc = RoundtableService(env.rt)
    svc._local_now = lambda: NOW
    with TestClient(create_app(svc)) as c:
        r = c.get("/api/chat/growth/V1")
        assert r.status_code == 200 and r.json()["timeline"] == [] and r.json()["due"] == WEEK
        r = c.post("/api/chat/growth/V1/run")
        assert r.status_code == 200 and r.json()["result"]["changes"] == 2
        assert c.post("/api/chat/growth/V1/run").status_code == 409
        first = r.json()["timeline"][0]["id"]
        r = c.post("/api/chat/growth/V1/revert", json={"id": first})
        assert r.status_code == 200 and r.json()["timeline"][0]["kind"] == "revert"
        assert c.post("/api/chat/growth/V1/revert", json={"id": 999}).status_code == 400
        assert c.get("/api/chat/growth/nobody").status_code == 400
        assert c.delete("/api/chat/growth/V1").json()["deleted"] == 1
