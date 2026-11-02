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


def test_new_fragment_shows_up_without_restart_and_bad_one_is_named(tmp_path):
    env = Env()
    head = tmp_path / "changelog.yaml"
    head.write_text("start: 3\n", "utf-8")
    (tmp_path / "changelog.d").mkdir()
    one = "date: 2026-11-02\npr: [7]\ntitle: 第一条\nadded: [新东西]\n"
    (tmp_path / "changelog.d" / "7.yaml").write_text(one, "utf-8")
    env.rt.changelog = head
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        assert c.get("/api/changelog").json()["latest"] == "3.0.0"
        two = "date: 2026-11-02\npr: [8]\ntitle: 第二条\nfixed: [修好了]\n"
        (tmp_path / "changelog.d" / "8.yaml").write_text(two, "utf-8")
        data = c.get("/api/changelog").json()
        assert data["latest"] == "3.0.1" and data["releases"][0]["title"] == "第二条"

        (tmp_path / "changelog.d" / "9.yaml").write_text("date: 2026-11-02\npr: [9]\n", "utf-8")
        r = c.get("/api/changelog")
        assert r.status_code == 500 and "changelog.d/9.yaml" in r.json()["detail"]
