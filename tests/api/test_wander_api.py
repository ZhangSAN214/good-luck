"""成员自己上网接口：状态、让她去逛、浏览记录、实时小图、叫停。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env
from ..core.wander.test_wander import HOME, SITE, pages, step


def env_with_web() -> Env:
    from roundtable.core.wander import FakeWeb

    env = Env()
    chat = env.config.roundtable.chat
    w = chat.wander.model_copy(update={"sites": [SITE], "step_wait_seconds": 0})
    rt = env.config.roundtable.model_copy(update={"chat": chat.model_copy(update={"wander": w})})
    env.rt.config = env.config.model_copy(update={"roundtable": rt})
    web = FakeWeb(pages())
    env.rt.wander_web = lambda: web
    return env


def test_wander_round_trip():
    env = env_with_web()
    env.fake.queue("b1", step("open", "首页有猫，点进去", link=1))
    env.fake.queue("b1", step("done", "", summary="看了猫咪合集"))
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        st = c.get("/api/wander").json()
        assert st["enabled"] and st["unavailable"] is None and st["running"] is None
        assert st["sites"] == [{"id": "bilibili", "name": "B站", "home": HOME}]
        assert any(m["id"] == "V1" and m["today"] == 0 for m in st["members"])
        assert c.get("/api/wander/frame").status_code == 204
        r = c.post("/api/wander/start", json={"member": "nobody"})
        assert r.status_code == 400
        r = c.post("/api/wander/start", json={"member": "V1", "site": "bilibili"})
        assert r.status_code == 200 and r.json()["running"]["member"] == "V1"
        deadline = time.monotonic() + 5
        while c.get("/api/wander").json()["running"] and time.monotonic() < deadline:
            time.sleep(0.02)
        log = c.get("/api/wander/log", params={"member": "V1"}).json()["messages"]
        kinds = [m["meta"]["wander"]["kind"] for m in log]
        assert kinds == ["start", "page", "summary", "end"]
        after = c.get("/api/wander/log", params={"member": "V1", "after": log[-2]["id"]}).json()
        assert [m["id"] for m in after["messages"]] == [log[-1]["id"]]
        assert c.get("/api/wander/log", params={"member": "nobody"}).status_code == 400
        assert c.get("/api/wander").json()["members"][0]["today"] in (0, 1)
        assert c.post("/api/wander/stop").json()["running"] is None
