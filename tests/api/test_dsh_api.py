"""dsh 工作台接口：状态、启动失败给原因、网页地址只给电脑本机。

dsh 是可拔掉的外设：这里只用替身，不依赖 core/dsh（CI「拔掉 dsh」任务也跑这个文件）。
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import LanAccess, RoundtableService

from ..core.orchestrator.conftest import Env
from .test_lan_api import PW, _fixed_status, clients

URL = "http://127.0.0.1:3080/?token=abc"


class FakeDsh:
    def __init__(self) -> None:
        self.state = "stopped"

    def status(self) -> dict[str, Any]:
        running = self.state == "running"
        return {
            "enabled": True,
            "unavailable": None,
            "state": self.state,
            "url": URL if running else None,
        }

    async def start(self) -> dict[str, Any]:
        self.state = "running"
        return self.status()

    async def stop(self, why: str = "") -> dict[str, Any]:
        self.state = "stopped"
        return self.status()

    async def check_now(self) -> dict[str, Any]:
        return self.status()

    async def aclose(self) -> None:
        self.state = "stopped"


def test_memory_db_reports_unavailable():
    with TestClient(create_app(RoundtableService(Env().rt))) as c:
        st = c.get("/api/dsh").json()
        assert st["state"] == "stopped" and st["unavailable"]
        r = c.post("/api/dsh/start")
        assert r.status_code == 409 and r.json()["detail"]


def test_url_only_for_this_pc(tmp_path):
    service = RoundtableService(Env().rt)
    service.dsh = FakeDsh()
    lan = LanAccess(tmp_path / "lan_access.json", enabled=True)
    lan.status = _fixed_status(lan.status)
    pc, pad = clients(create_app(service, lan=lan))
    with pc, pad:
        assert pc.post("/api/lan/password", json={"password": PW}).status_code == 200
        assert pad.post("/api/lan/login", json={"password": PW}).status_code == 200
        assert pc.post("/api/dsh/start").json()["url"] == URL
        st = pad.get("/api/dsh").json()
        assert st["state"] == "running" and st["url"] is None and st["local_only"]
        assert pc.post("/api/dsh/check").json()["url"] == URL
        assert pc.post("/api/dsh/stop").json()["state"] == "stopped"


def test_service_without_dsh_module():
    service = RoundtableService(Env().rt)
    service.dsh = None  # 等同于删掉了 core/dsh
    with TestClient(create_app(service)) as c:
        st = c.get("/api/dsh").json()
        assert st["enabled"] is False and "core/dsh" in st["unavailable"]
        assert c.post("/api/dsh/start").status_code == 409
        assert c.post("/api/dsh/stop").json()["state"] == "stopped"
