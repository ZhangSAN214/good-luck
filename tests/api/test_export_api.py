"""导出成果：各格式下载、文件名、匿名会话揭晓前不含模型名；导出不调用模型。"""

from __future__ import annotations

from .test_app import SHORT, client, leaks, sse_events


def test_export_formats_over_http():
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        r = c.post("/api/sessions", json={"question": SHORT, "seed": 2, "anonymous": True})
        sid = r.json()["session_id"]
        assert sse_events(c, sid)[-1]["status"] == "completed"
        calls = len(c.get(f"/api/sessions/{sid}").json()["calls"])
        heads = {
            "docx": b"PK",
            "pptx": b"PK",
            "pdf": b"%PDF",
            "md": b"# ",
            "html": b"<!doctype html>",
        }
        for fmt, head in heads.items():
            r = c.get(f"/api/sessions/{sid}/export", params={"format": fmt})
            assert r.status_code == 200 and r.content.startswith(head), fmt
            disposition = r.headers["content-disposition"]
            assert disposition.startswith("attachment;") and disposition.endswith(f".{fmt}")
        md = c.get(f"/api/sessions/{sid}/export", params={"format": "md"}).text
        assert leaks(env, md) == []
        assert len(c.get(f"/api/sessions/{sid}").json()["calls"]) == calls  # 不调用模型

        assert c.get(f"/api/sessions/{sid}/export", params={"format": "exe"}).status_code == 400
        assert c.get("/api/sessions/nope/export").status_code == 404


def test_export_options_style_and_save_to_folder(tmp_path):
    env, c = client(confirm_threshold_usd=100.0)
    with c:
        sid = c.post("/api/sessions", json={"question": SHORT, "seed": 2}).json()["session_id"]
        assert sse_events(c, sid)[-1]["status"] == "completed"
        opts = c.get("/api/export/options").json()
        assert opts["can_save_to_folder"] and opts["folder"] == "" and opts["suggested_folder"]
        assert {f["value"] for f in opts["formats"]} == {"md", "docx", "pptx", "pdf", "html"}

        r = c.get(
            f"/api/sessions/{sid}/export",
            params={"format": "html", "theme": "serif", "size": "large", "question": "false"},
        )
        assert r.status_code == 200 and "--accent:#7B2D26" in r.text
        bad = c.get(f"/api/sessions/{sid}/export", params={"format": "md", "theme": "pink"})
        assert bad.status_code == 400 and "配色" in bad.json()["detail"]

        folder = tmp_path / "导出" / "子目录"
        body = {"format": "docx", "folder": str(folder), "process": True}
        first = c.post(f"/api/sessions/{sid}/export/save", json=body).json()
        second = c.post(f"/api/sessions/{sid}/export/save", json=body).json()
        assert first["path"] != second["path"] and second["name"].endswith(" (2).docx")
        assert len(list(folder.iterdir())) == 2
        assert c.get("/api/export/options").json()["folder"] == str(folder)  # 记住文件夹
        rel = c.post(f"/api/sessions/{sid}/export/save", json={"folder": "相对路径"})
        assert rel.status_code == 400


def test_save_to_folder_is_refused_for_other_devices(tmp_path):
    from fastapi.testclient import TestClient

    from roundtable.api.app import create_app
    from roundtable.core.service import RoundtableService

    from ..core.orchestrator.conftest import Env

    env = Env()
    tablet = TestClient(create_app(RoundtableService(env.rt)), client=("192.168.1.20", 5000))
    with tablet:
        r = tablet.post("/api/sessions/x/export/save", json={"folder": str(tmp_path)})
        assert r.status_code in (401, 403)
        assert not list(tmp_path.iterdir())
