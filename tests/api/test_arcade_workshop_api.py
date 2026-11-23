"""游戏工坊接口：状态、开做、做好后在沙箱里取文件、删除；提示词能用真的模板拼出来。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.arcade.workshop import Workshop
from roundtable.core.config.schema import SupercomputerRules, SuperWorker
from roundtable.core.service import RoundtableService
from roundtable.core.supercomputer import Supercomputer

from ..core.arcade.test_workshop import Agents
from ..core.orchestrator.conftest import Env
from ..core.supercomputer.test_engine import Launcher, render


def service_with(tmp_path) -> RoundtableService:
    service = RoundtableService(Env().rt)
    workers = [
        SuperWorker(id=w, label=w, argv=[w], skills={"frontend": 0.5, "review": 0.5})
        for w in ("claude", "opencode-ds", "codex")
    ]
    service.supercomputer = Supercomputer(
        SupercomputerRules(workers=workers),
        root=tmp_path / "super",
        render=render,
        launchers={"command": Launcher()},
        runner=Agents(),
        seed=3,
    )
    service.workshop = Workshop(
        lambda: service.rt.config.roundtable.arcade.workshop,
        root=tmp_path / "ws",
        supercomputer=service.supercomputer,
        render=render,
        check=lambda folder: [],
    )
    return service


def test_real_prompts_render():
    service = RoundtableService(Env().rt)
    text = service._super_render("arcade_workshop_build", request="<b>贪吃蛇</b>", max_kb="800")
    assert "<question>" in text and "贪吃蛇" in text and "800 KB" in text
    text = service._super_render("arcade_workshop_review", request="r", notes="n", issues="- x")
    assert "<issues>" in text
    assert "<issues>" in service._super_render("arcade_workshop_fix", request="r", issues="- x")


def test_memory_db_is_unavailable():
    with TestClient(create_app(RoundtableService(Env().rt))) as c:
        st = c.get("/api/arcade/workshop").json()
        assert "内存数据库" in st["unavailable"] and st["games"] == []
        assert c.post("/api/arcade/workshop", json={"text": "做个游戏"}).status_code == 409


def test_make_serve_delete(tmp_path):
    with TestClient(create_app(service_with(tmp_path))) as c:
        assert c.post("/api/arcade/workshop", json={"text": ""}).status_code == 422
        st = c.post("/api/arcade/workshop", json={"text": "收集星星"}).json()
        assert st["busy"] and st["job"]["status"] == "building"
        end = time.monotonic() + 5
        while c.get("/api/arcade/workshop").json()["busy"] and time.monotonic() < end:
            time.sleep(0.02)
        st = c.get("/api/arcade/workshop").json()
        assert st["job"]["status"] == "done"
        gid = st["games"][0]["id"]
        r = c.get(f"/arcade-games/{gid}/game.html")
        assert r.status_code == 200 and "<canvas>" in r.text
        assert "sandbox allow-scripts" in r.headers["content-security-policy"]
        assert c.get(f"/arcade-games/{gid}/..%2Fjob.json").status_code == 404
        assert c.get("/arcade-games/nope/game.html").status_code == 404
        assert c.delete(f"/api/arcade/workshop/games/{gid}").json()["games"] == []
        assert c.delete(f"/api/arcade/workshop/games/{gid}").status_code == 404
        assert c.post("/api/arcade/workshop/cancel").status_code == 200
