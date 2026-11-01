"""自检接口：电脑上能看结论和体检报告；平板上一律拒绝（报告里有本机路径）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import LanAccess, RoundtableService

from ..core.orchestrator.conftest import Env

PC = ("127.0.0.1", 50000)
PAD = ("192.168.1.20", 50001)


def test_firstaid_endpoints():
    with TestClient(create_app(RoundtableService(Env().rt)), client=PC) as c:
        r = c.get("/api/firstaid")
        assert r.status_code == 200
        body = r.json()
        assert {s["id"] for s in body["scenes"]} == {"start", "hang", "undo", "data"}
        assert body["findings"] and body["headline"]
        r = c.get("/api/firstaid/report")
        assert r.status_code == 200 and "圆桌体检报告" in r.json()["text"]


def test_firstaid_refused_on_tablet(tmp_path):
    lan = LanAccess(tmp_path / "lan_access.json", enabled=True)
    app = create_app(RoundtableService(Env().rt), lan=lan)
    pad = TestClient(app, client=PAD, base_url="http://192.168.1.5:8000")
    assert pad.get("/api/firstaid").status_code in (401, 403)
    assert pad.get("/api/firstaid/report").status_code in (401, 403)
