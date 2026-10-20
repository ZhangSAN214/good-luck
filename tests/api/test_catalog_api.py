"""模型人才市场接口：GET 读最近结果，POST 重新查询；只提示，不改配置。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_candidates_refresh_and_errors():
    env = Env()
    calls = []

    async def fetch(url):
        calls.append(url)
        return {
            "data": [
                {"id": m.id, "created": 1, "pricing": {"prompt": "0.000001", "completion": "0"}}
                for m in env.config.models.models
            ]
        }

    env.rt.catalog.fetch = fetch
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.get("/api/models/candidates")
        assert r.status_code == 200 and r.json() == {
            "fetched_at": None,
            "stale": True,
            "available": True,
        }
        r = c.post("/api/models/candidates/refresh")
        assert r.status_code == 200
        data = r.json()
        assert data["stale"] is False and data["source"] == "c"
        assert {p["model_id"] for p in data["price_changes"]} >= {"b1"}
        assert c.get("/api/models/candidates").json()["fetched_at"] == data["fetched_at"]
        assert len(calls) == 1

        async def broken(url):
            raise OSError("boom")

        env.rt.catalog.fetch = broken
        r = c.post("/api/models/candidates/refresh")
        assert r.status_code == 502 and "OSError" in r.json()["detail"]
