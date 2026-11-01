"""超级电脑接口：状态、交活、看结果、改每天次数、叫醒、叫停。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService
from roundtable.core.supercomputer import Supercomputer

from ..core.orchestrator.conftest import Env
from ..core.supercomputer.test_engine import Agents, Launcher, render, rules


def service_with(tmp_path, agents) -> RoundtableService:
    service = RoundtableService(Env().rt)
    lau = Launcher()
    service.supercomputer = Supercomputer(
        rules(),
        root=tmp_path / "super",
        render=render,
        launchers={"command": lau, "dsh": lau},
        runner=agents,
        seed=3,
    )
    return service


def wait_done(c, deadline=5.0):
    end = time.monotonic() + deadline
    while c.get("/api/super").json()["busy"] and time.monotonic() < end:
        time.sleep(0.02)


def test_round_trip(tmp_path):
    with TestClient(create_app(service_with(tmp_path, Agents()))) as c:
        st = c.get("/api/super").json()
        assert st["unavailable"] is None and [w["id"] for w in st["workers"]] == ["d", "c", "k"]
        assert c.post("/api/super/tasks", json={"text": ""}).status_code == 422
        r = c.post("/api/super/tasks", json={"text": "加个接口"})
        assert r.status_code == 200 and r.json()["task"]["text"] == "加个接口"
        wait_done(c)
        st = c.get("/api/super").json()
        task = st["task"]
        assert task["status"] == "done" and task["result"]
        assert st["recent"][0]["id"] == task["id"]
        assert c.get(f"/api/super/tasks/{task['id']}").json()["status"] == "done"
        assert c.get("/api/super/tasks/nope").status_code == 404
        assert c.get("/api/super/tasks/..%2F..").status_code == 404


def test_limits_wake_and_cancel(tmp_path):
    with TestClient(create_app(service_with(tmp_path, Agents(delay=5)))) as c:
        r = c.put("/api/super/workers/k/limit", json={"limit": 3})
        assert {w["id"]: w["limit"] for w in r.json()["workers"]}["k"] == 3
        assert c.put("/api/super/workers/x/limit", json={"limit": 3}).status_code == 400
        assert c.put("/api/super/workers/k/limit", json={"limit": -1}).status_code == 422
        assert c.post("/api/super/workers/k/wake").status_code == 200
        c.post("/api/super/tasks", json={"text": "慢活"})
        assert c.post("/api/super/tasks", json={"text": "又一件"}).status_code == 409
        st = c.post("/api/super/cancel").json()
        assert not st["busy"] and st["task"]["status"] == "cancelled"


def test_memory_db():
    with TestClient(create_app(RoundtableService(Env().rt))) as c:
        st = c.get("/api/super").json()
        assert "内存数据库" in st["unavailable"]
        assert c.post("/api/super/tasks", json={"text": "活"}).status_code == 400


def test_service_wiring():
    """服务层拼的超级电脑：真提示词、命令行助手拿不到圆桌的 key。"""
    service = RoundtableService(Env().rt)
    sc = service.supercomputer
    text = sc.render("super_split", task="做个网站", kinds="code、docs", max_subtasks="3")
    assert "<question>\n做个网站\n</question>" in text and "最多 3 件" in text
    lau = sc.launchers["command"]
    assert "DEEPSEEK_API_KEY" in lau.drop
    assert ("dsh" in sc.launchers) == (service.dsh is not None)
