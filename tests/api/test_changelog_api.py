"""公告栏接口：只读；数据文件有错时给出带文件名的说明。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_changelog_reads_the_file_on_each_request(tmp_path):
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.get("/api/changelog")
        assert r.status_code == 200
        data = r.json()
        assert data["latest"] == data["releases"][0]["version"] and data["releases"][0]["title"]
        assert data["releases"][0]["id"].startswith("#")

        broken = tmp_path / "changelog.yaml"
        broken.write_text("start: 3\nreleases: []\n", "utf-8")
        env.rt.changelog = broken
        r = c.get("/api/changelog")
        assert r.status_code == 500 and "changelog.yaml" in r.json()["detail"]
