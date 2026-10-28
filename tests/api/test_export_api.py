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
