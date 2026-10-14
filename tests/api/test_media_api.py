"""媒体输出的 HTTP 接口：状态、提交前预估、提交、确认卡片、播放（Range）与下载、匿名。"""

from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import MEDIUM, Env
from .test_app import leaks, sse_events

PICKS = {"tier": "custom", "models": ["b1", "b2", "b3", "f1"], "coordinator": "f1"}


def client(**kw) -> tuple[Env, TestClient]:
    kw.setdefault("confirm_threshold_usd", 100.0)
    env = Env(with_media=True, **kw)
    return env, TestClient(create_app(RoundtableService(env.rt)))


def wait_rest(c: TestClient, sid: str) -> dict:
    for _ in range(100):
        data = c.get(f"/api/sessions/{sid}").json()
        if not data["running"]:
            return data
        time.sleep(0.05)
    raise AssertionError("等待超时")


def test_status_lists_media_availability_without_model_names():
    env, c = client()
    with c:
        media = c.get("/api/status").json()["media"]
        assert media["enabled"] and media["confirm_video"] and media["default_tier"] == "budget"
        assert set(media["kinds"]) == {"image", "speech", "video"}
        image = media["kinds"]["image"]
        assert image["available"] and image["tiers"] == {"budget": True, "flagship": True}
        assert media["kinds"]["speech"]["tiers"] == {"budget": True, "flagship": True}  # 退到另一档
        assert leaks(env, json.dumps(media)) == []


def test_status_reports_unavailable_media():
    env = Env()  # 没有媒体模型
    c = TestClient(create_app(RoundtableService(env.rt)))
    with c:
        kinds = c.get("/api/status").json()["media"]["kinds"]
        assert (
            not kinds["video"]["available"] and "没有可用的视频生成模型" in kinds["video"]["reason"]
        )
        r = c.post("/api/sessions", json={"question": MEDIUM, "media": "video"})
        assert r.status_code == 400 and "视频" in r.json()["detail"]


def test_rejects_bad_media_arguments():
    env, c = client()
    with c:
        assert (
            c.post("/api/sessions", json={"question": MEDIUM, "media": "hologram"}).status_code
            == 400
        )
        r = c.post(
            "/api/sessions", json={"question": MEDIUM, "media": "image", "workflow": "collab"}
        )
        assert r.status_code == 400 and "协同模式" in r.json()["detail"]
        r = c.post(
            "/api/sessions", json={"question": MEDIUM, "media": "image", "media_tier": "gold"}
        )
        assert r.status_code == 400


def test_estimate_includes_media_cost():
    env, c = client()
    with c:
        base = {"question": MEDIUM, "workflow": "discussion", "tier": "budget"}
        plain = c.post("/api/estimate", json=base).json()["options"]
        video = c.post("/api/estimate", json={**base, "media": "video"}).json()["options"]
        cheap = next(o for o in plain if o["plan"] == "budget")
        dear = next(o for o in video if o["plan"] == "budget")
        assert dear["estimate_usd"] > cheap["estimate_usd"]
        assert "media" in [s["step"] for s in dear["steps"]]
        assert "media" not in [s["step"] for s in cheap["steps"]]


def test_image_flow_with_playback_download_and_range():
    env, c = client()
    with c:
        sid = c.post(
            "/api/sessions", json={"question": MEDIUM, "media": "image", "seed": 3, **PICKS}
        ).json()["session_id"]
        events = sse_events(c, sid)
        assert any(e["type"] == "media_started" for e in events)
        assert any(e["type"] == "media_done" for e in events)
        data = wait_rest(c, sid)
        assert data["status"] == "completed" and data["media_kind"] == "image"
        job = data["media"][0]
        assert job["kind"] == "image" and job["round"] == 1 and job["state"] == "completed"
        assert job["cost_usd"] > 0 and job["model_id"] == "mi1"  # 匿名关闭：显示模型
        r = c.get(f"/api/sessions/{sid}/files/{job['file_id']}?inline=1")
        assert (
            r.headers["content-type"] == "image/png"
            and r.headers["x-content-type-options"] == "nosniff"
        )


def test_video_confirmation_card_then_playback_with_ranges():
    env, c = client()
    with c:
        sid = c.post(
            "/api/sessions", json={"question": MEDIUM, "media": "video", "seed": 3, **PICKS}
        ).json()["session_id"]
        data = wait_rest(c, sid)
        assert data["status"] == "awaiting_confirmation"
        card = data["pending_checkpoint"]["card"]
        assert card["kind"] == "media" and [o["key"] for o in card["options"]] == [
            "generate",
            "skip",
            "stop",
        ]
        assert (
            c.post(f"/api/sessions/{sid}/respond", json={"response": "generate"}).status_code == 202
        )
        data = wait_rest(c, sid)
        assert data["status"] == "completed"
        job = data["media"][0]
        url = f"/api/sessions/{sid}/files/{job['file_id']}?inline=1"
        full = c.get(url)
        assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
        assert full.headers["accept-ranges"] == "bytes"
        part = c.get(url, headers={"Range": "bytes=0-9"})
        assert part.status_code == 206 and part.content == full.content[:10]
        assert part.headers["content-range"] == f"bytes 0-9/{len(full.content)}"
        tail = c.get(url, headers={"Range": "bytes=-4"})
        assert tail.status_code == 206 and tail.content == full.content[-4:]
        assert c.get(url, headers={"Range": "bytes=99999-"}).status_code == 416
        # 不带 inline：始终是附件下载
        dl = c.get(url.replace("?inline=1", ""))
        assert dl.headers["content-disposition"].startswith("attachment")
        assert dl.headers["content-type"] == "application/octet-stream"
        preview = c.get(f"/api/sessions/{sid}/files/{job['file_id']}/preview").json()
        assert preview["type"] == "video"


def test_anonymous_media_hides_models_and_prompts_are_masked():
    env, c = client()
    with c:
        sid = c.post(
            "/api/sessions",
            json={"question": MEDIUM, "media": "image", "seed": 3, "anonymous": True, **PICKS},
        ).json()["session_id"]
        events = sse_events(c, sid)
        data = wait_rest(c, sid)
        job = data["media"][0]
        assert job["model_id"] is None and job["channel"] is None
        assert leaks(env, json.dumps(events, ensure_ascii=False) + json.dumps(data)) == []
        revealed = c.post(f"/api/sessions/{sid}/reveal").json()
        assert revealed["media"][0]["model_id"] == "mi1"


def test_style_references_flow_over_http():
    """上传的图片默认是风格参考，可以取消勾选；风格规范、参考图张数出现在会话详情里。"""
    from ..core.attachments.samples import PNG
    from ..core.orchestrator.test_style import with_style_replies

    env, c = client()
    with_style_replies(env)
    with c:
        up = c.post("/api/uploads", params={"name": "ref.png"}, content=PNG).json()
        assert up["style_ref"] is True
        sid = c.post(
            "/api/sessions",
            json={
                "question": MEDIUM,
                **PICKS,
                "media": "image",
                "media_tier": "flagship",
                "attachments": [up["id"]],
            },
        ).json()["session_id"]
        data = wait_rest(c, sid)
        assert data["status"] == "completed"
        assert data["attachments"][0]["style_ref"] is True
        spec = [o for o in data["outputs"] if o["kind"] == "style_spec"]
        assert spec and len(json.loads(spec[0]["content"])["checklist"]) == 6
        assert data["media"][0]["reference_count"] == 1 and data["media"][0]["warning"] is None
        assert "style" in data["tables"][0]["pipeline"]
        # 取消勾选：只是普通附件，没有风格规范，也不传参考图
        up2 = c.post("/api/uploads", params={"name": "ref2.png"}, content=PNG).json()
        sid2 = c.post(
            "/api/sessions",
            json={
                "question": MEDIUM,
                **PICKS,
                "media": "image",
                "attachments": [up2["id"]],
                "style_refs": [],
            },
        ).json()["session_id"]
        plain = wait_rest(c, sid2)
        assert plain["attachments"][0]["style_ref"] is False
        assert not [o for o in plain["outputs"] if o["kind"] == "style_spec"]
        assert plain["media"][0]["reference_count"] == 0
        # 风格参考必须是本次提交的附件
        bad = c.post(
            "/api/sessions",
            json={"question": MEDIUM, **PICKS, "attachments": [], "style_refs": ["nope"]},
        )
        assert bad.status_code == 400 and "风格参考" in bad.json()["detail"]
