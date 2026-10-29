"""成员引擎的自我档案接口：查看、修改、锁定、撤回、清空；不允许的修改返回 400。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_profile_round_trip():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        data = c.get("/api/chat/profiles/V1").json()
        assert data["version"] == 0 and data["history"] == [] and "likes" in data["sections"]
        r = c.patch("/api/chat/profiles/V1", json={"patch": {"likes": {"音乐": "后摇"}}})
        assert r.status_code == 200 and r.json()["version"] == 1
        jazz = {"patch": {"likes": {"音乐": "爵士"}}, "reason": "试试"}
        c.patch("/api/chat/profiles/V1", json=jazz)
        data = c.post("/api/chat/profiles/V1/lock", json={"path": "likes.音乐"}).json()
        assert data["locked"] == ["likes.音乐"]
        data = c.post("/api/chat/profiles/V1/revert", json={"version": 1}).json()
        assert data["data"]["likes"] == {"音乐": "后摇"}
        assert [h["version"] for h in data["history"]] == [4, 3, 2, 1]
        assert data["history"][2]["reason"] == "试试"
        assert c.delete("/api/chat/profiles/V1").json()["deleted"] == 4
        assert c.get("/api/chat/profiles/V1").json()["version"] == 0


def test_profile_errors():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        assert c.get("/api/chat/profiles/nobody").status_code == 400
        r = c.patch("/api/chat/profiles/V1", json={"patch": {"乱写": {"a": 1}}})
        assert r.status_code == 400 and "乱写" in r.json()["detail"]
        assert c.post("/api/chat/profiles/V1/revert", json={"version": 3}).status_code == 400
        assert c.post("/api/chat/profiles/V1/lock", json={"path": "abc"}).status_code == 400
        assert c.patch("/api/chat/profiles/V1", json={"patch": {}, "x": 1}).status_code == 422
