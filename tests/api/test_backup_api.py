"""整库备份接口：看列表、存档点、恢复；平板上一律拒绝；不认识的文件名报 400。"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.backup import DbBackup
from roundtable.core.service import LanAccess, RoundtableService

from ..core.orchestrator.conftest import Env

PC = ("127.0.0.1", 50000)
PAD = ("192.168.1.20", 50001)


def test_backup_endpoints(tmp_path):
    env = Env()
    svc = RoundtableService(env.rt)
    svc._local_now = lambda: datetime(2026, 11, 2, 9, 0)
    svc.backup = DbBackup(env.rt.repo.conn, tmp_path / "bk")
    assert svc.backup_tick()["kind"] == "daily"
    assert svc.backup_tick() is None
    with TestClient(create_app(svc), client=PC) as c:
        r = c.get("/api/backup")
        assert r.status_code == 200 and len(r.json()["files"]) == 1
        r = c.post("/api/backup/checkpoint", json={"label": "试跑前"})
        assert r.status_code == 200 and r.json()["saved"]["label"] == "试跑前"
        name = r.json()["saved"]["name"]
        r = c.post("/api/backup/restore", json={"name": name})
        assert r.status_code == 200 and r.json()["restart"] is True
        assert c.post("/api/backup/restore", json={"name": "../x.db"}).status_code == 400


def test_backup_refused_on_tablet(tmp_path):
    lan = LanAccess(tmp_path / "lan_access.json", enabled=True)
    app = create_app(RoundtableService(Env().rt), lan=lan)
    pad = TestClient(app, client=PAD, base_url="http://192.168.1.5:8000")
    assert pad.get("/api/backup").status_code in (401, 403)
    assert pad.post("/api/backup/restore", json={"name": "x"}).status_code in (401, 403)
