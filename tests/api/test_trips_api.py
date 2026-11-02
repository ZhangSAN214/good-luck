"""旅行与 B 站接口：状态、开关、让她看一个视频；私信和动态只在本机列表里。"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.member.checkin import Fetched
from roundtable.core.service import RoundtableService, ServiceError

from ..core.orchestrator.conftest import Env
from ..core.perception.media import make_video


def test_trips_status_and_switches():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        st = c.get("/api/trips").json()
        assert st["enabled"] and st["bili"]["auto"] is True and st["bili"]["photo"] is False
        assert st["bili"]["external"] is False and st["bili"]["quota"]["dm"] >= 1
        assert set(st["travel"]) >= {"V1"} and st["checkins"] == []
        st = c.post("/api/trips/switches", json={"photo": True}).json()
        assert st["bili"]["photo"] is True
        st = c.post("/api/trips/switches", json={"auto": False, "photo": False}).json()
        assert st["bili"]["auto"] is False and st["bili"]["photo"] is False
        r = c.post("/api/trips/bili/watch", json={"member": "V1", "url": "https://example.com/x1"})
        assert r.status_code == 400  # 不是 B 站视频
        r = c.post(
            "/api/trips/bili/watch",
            json={"member": "nobody", "url": "https://www.bilibili.com/video/BV1xx411c7mD"},
        )
        assert r.status_code == 400
        assert c.post("/api/trips/switches", json={"other": 1}).status_code == 422


async def test_watch_round_trip_through_the_service(tmp_path):
    """接口 /api/trips/bili/watch 只是包一层：这里直接走服务，看完后列表里有私信、打卡记录。"""
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
    svc = RoundtableService(env.rt)
    rec = await svc.bili_watch("V1", "https://www.bilibili.com/video/BV1xx411c7mD")
    assert rec["title"] == "猫咪合集"
    st = svc.trips()
    assert [o["kind"] for o in st["bili"]["outbox"]] == ["dm"]
    assert st["checkins"][0]["ok"] is False and st["checkins"][0]["reason"] == "多人群像"

    async def broken(url: str) -> Fetched:
        from roundtable.core.member.checkin import BiliUnavailable

        raise BiliUnavailable("没有 yt-dlp")

    env.rt.bili_fetch = broken
    try:
        await svc.bili_watch("V2", "https://www.bilibili.com/video/BV1xx411c7mE")
    except ServiceError as e:
        assert e.status == 409
    else:
        raise AssertionError("没看成要报 409")
