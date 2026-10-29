"""主时区接口：读、改、报错。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_get_and_put_home_tz():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        d = c.get("/api/home-tz").json()
        assert d["zone"] == "local" and d["chosen"] is False
        d = c.put("/api/home-tz", json={"zone": "Asia/Shanghai"}).json()
        assert d["zone"] == "Asia/Shanghai" and d["chosen"] and d["offset_minutes"] == 480
        assert c.get("/api/home-tz").json()["zone"] == "Asia/Shanghai"
        r = c.put("/api/home-tz", json={"zone": "Mars/Base"})
        assert r.status_code == 400 and "时区" in r.json()["detail"]
        assert c.put("/api/home-tz", json={"zone": "x", "extra": 1}).status_code == 422
