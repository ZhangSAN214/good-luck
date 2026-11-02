"""旅行与 B 站接口：状态、开关、让她看一个视频；私信和动态只在本机列表里。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.member.checkin import Fetched
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env
from ..core.perception.media import make_video


def test_trips_round_trip(tmp_path):
    env = Env(with_media=True)
    env.rt.chat_media_dir = tmp_path / "media"

    async def fetch(url: str) -> Fetched:
        return Fetched(make_video(4), "猫咪合集", 4.0)

    env.rt.bili_fetch = fetch
    env.fake.queue(
        "b1",
        json.dumps({"can": False, "reject": "多人群像"}, ensure_ascii=False),
        json.dumps({"dm": "这个猫好懒，你看看", "post": ""}, ensure_ascii=False),
    )
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        st = c.get("/api/trips").json()
        assert st["enabled"] and st["bili"]["auto"] is True and st["bili"]["photo"] is False
        assert st["bili"]["external"] is False and st["bili"]["quota"]["dm"] >= 1
        assert set(st["travel"]) >= {"V1"} and st["checkins"] == []
        st = c.post("/api/trips/switches", json={"photo": True}).json()
        assert st["bili"]["photo"] is True
        c.post("/api/trips/switches", json={"photo": False})
        r = c.post("/api/trips/bili/watch", json={"member": "V1", "url": "https://example.com/x1"})
        assert r.status_code == 400  # 不是 B 站视频
        url = "https://www.bilibili.com/video/BV1xx411c7mD"
        r = c.post("/api/trips/bili/watch", json={"member": "V1", "url": url})
        assert r.status_code == 200 and r.json()["title"] == "猫咪合集"
        st = c.get("/api/trips").json()
        assert [o["kind"] for o in st["bili"]["outbox"]] == ["dm"]
        assert st["checkins"][0]["ok"] is False and st["checkins"][0]["reason"] == "多人群像"
        st = c.post("/api/trips/switches", json={"auto": False}).json()
        assert st["bili"]["auto"] is False
