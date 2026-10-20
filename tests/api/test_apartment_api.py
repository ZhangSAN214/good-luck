"""公寓楼接口：按浏览器本地时间给出住户位置；匿名未揭晓的会话不暴露是谁。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_apartment_endpoint():
    env = Env()
    repo = env.rt.repo
    secret = repo.create_session("q", seed=1, anonymous=True)
    repo.add_seats(secret, 0, {"甲": "b1"})
    repo.set_status(secret, "running")
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.get("/api/apartment", params={"hour": 3, "weekday": 1})
        assert r.status_code == 200
        data = r.json()
        assert data["hidden_meetings"] == 1
        assert all(p["activity"] == "sleep" for p in data["residents"])
        assert secret not in r.text
        assert c.get("/api/apartment", params={"hour": 24, "weekday": 1}).status_code == 400
        assert c.get("/api/apartment").status_code == 422
