"""插件榜接口：只读；数据文件有错时给出带文件名的说明。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_board_reads_the_file_on_each_request(tmp_path):
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.get("/api/plugins/board")
        assert r.status_code == 200
        data = r.json()
        assert data["entries"][0]["rank"] == 1 and data["criteria"]["code_min_stars"] >= 1

        broken = tmp_path / "plugin_board.yaml"
        broken.write_text("updated: 2026-10-26\nentries: []\n", "utf-8")
        env.rt.plugin_board = broken
        r = c.get("/api/plugins/board")
        assert r.status_code == 500 and "plugin_board.yaml" in r.json()["detail"]
