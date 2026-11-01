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


def test_butler_endpoints(tmp_path):
    """管家：定时活、等你确认、批准、排队、日报、审计。"""
    service = service_with(tmp_path, Agents(delay=0.3))
    with TestClient(create_app(service)) as c:
        st = c.get("/api/super").json()["butler"]
        assert st["autonomy"] == "low" and st["schedules"] == [] and st["audit"]["count"] == 0
        assert c.post("/api/super/schedules", json={"text": "x", "at": "9点"}).status_code == 422
        r = c.post("/api/super/schedules", json={"text": "给接口加分页", "at": "09:00"})
        sched = r.json()["butler"]["schedules"][0]
        assert sched["risk"] == "medium" and sched["when"] == "每天 09:00"
        sid = sched["id"]
        r = c.put(f"/api/super/schedules/{sid}", json={"enabled": False})
        assert r.json()["butler"]["schedules"][0]["enabled"] is False
        assert c.put("/api/super/schedules/nope", json={"enabled": True}).status_code == 404
        # 现在跑：你发起的，直接干；再交一件选排队
        assert c.post(f"/api/super/schedules/{sid}/run").json()["busy"]
        assert c.post("/api/super/tasks", json={"text": "又一件"}).status_code == 409
        r = c.post("/api/super/tasks", json={"text": "又一件", "queue": True})
        q = r.json()["butler"]["queue"]
        assert q[0]["text"] == "又一件"
        assert c.delete(f"/api/super/queue/{q[0]['id']}").json()["butler"]["queue"] == []
        assert c.delete("/api/super/queue/nope").status_code == 404
        wait_done(c)
        # 等你确认的：直接塞一条，批准后开干
        service.butler.data["pending"].append(
            {"id": "p1", "ref": sid, "text": "改登录页", "risk": "medium", "why": "测试"}
        )
        assert c.post("/api/super/pending/p1/approve").json()["busy"]
        assert c.post("/api/super/pending/p1/reject").status_code == 404
        wait_done(c)
        rep = c.get("/api/super/report").json()
        assert rep["tasks"] == 2 and "超级电脑日报" in rep["markdown"]
        assert c.get("/api/super/report?day=昨天").status_code == 400
        audit = c.get("/api/super/audit").json()
        assert audit["check"]["ok"] and audit["recent"][0]["act"] == "finish"
        assert {"schedule_add", "schedule_run", "queue", "approve"} <= {
            e["act"] for e in audit["recent"]
        }
        assert c.delete(f"/api/super/schedules/{sid}").json()["butler"]["schedules"] == []
        assert service.super_tick() is not None


def test_butler_memory_db():
    with TestClient(create_app(RoundtableService(Env().rt))) as c:
        assert c.get("/api/super").json()["butler"]["schedules"] == []
        assert RoundtableService(Env().rt).super_tick() == {}
