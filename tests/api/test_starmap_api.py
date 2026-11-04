"""星图接口：圆桌星图、成员的人生星图（成长改动连到它改的设定）；不认识的成员报 400。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.member.growth.common import NOW, fake_growth, setup
from ..core.orchestrator.conftest import Env


def test_starmap_endpoints():
    env = Env()
    setup(env)
    fake_growth(env)
    svc = RoundtableService(env.rt)
    svc._local_now = lambda: NOW
    with TestClient(create_app(svc)) as c:
        assert c.post("/api/chat/growth/V1/run").status_code == 200
        r = c.get("/api/starmap/V1")
        assert r.status_code == 200
        life = r.json()
        assert life["kind"] == "life" and life["member"] == "V1"
        assert {g["label"] for g in life["groups"]} >= {"身份", "性格", "记忆", "成长"}
        growth = [n for n in life["nodes"] if n["group"] == "growth" and n["kind"] == "star"]
        assert growth
        ids = {n["id"] for n in growth}
        assert any(
            link["kind"] == "relate" and (link["a"] in ids or link["b"] in ids)
            for link in life["links"]
        )
        assert c.get("/api/starmap/nobody").status_code == 400

        r = c.get("/api/starmap")
        assert r.status_code == 200
        rt = r.json()
        assert rt["kind"] == "roundtable"
        members = [n for n in rt["nodes"] if n["kind"] == "member"]
        assert {n["member"] for n in members} == {m.id for m in svc.chats.everyone()}
        assert {g["key"] for g in rt["groups"]} >= {"hub", "work", "home", "chat", "bench"}
