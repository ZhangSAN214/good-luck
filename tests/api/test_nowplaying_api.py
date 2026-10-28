"""音乐卡片接口：/api/nowplaying（读、封面、控制）与 /api/music/covers。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.nowplaying import FakeMediaSource, Playing
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 20


def _client(tmp_path, src) -> TestClient:
    env = Env(standee_file=tmp_path / "standee.json")
    env.rt.nowplaying_source = src
    return TestClient(create_app(RoundtableService(env.rt)))


def test_nowplaying_read_cover_and_control(tmp_path):
    src = FakeMediaSource(
        [
            Playing(
                app_id="QQMusic.exe",
                title="夜航星",
                status="playing",
                can=frozenset({"pause", "next"}),
                cover=PNG,
            )
        ]
    )
    with _client(tmp_path, src) as c:
        _read_and_control(c, src)


def _read_and_control(c, src):
    v = c.get("/api/nowplaying").json()
    assert v["now"]["title"] == "夜航星"
    r = c.get(v["now"]["cover"])
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    v = c.post("/api/nowplaying/control", json={"action": "toggle"}).json()
    assert v["now"]["status"] == "paused"
    assert c.post("/api/nowplaying/control", json={"action": "rm"}).status_code == 422
    # 没有播放器时：409，说明原因
    src.rows.clear()
    r = c.post("/api/nowplaying/control", json={"action": "next"})
    assert r.status_code == 409 and "播放器没有响应" in r.json()["detail"]
    assert c.get("/api/nowplaying/cover").status_code == 404


def test_music_covers_listing_and_files(tmp_path):
    with _client(tmp_path, FakeMediaSource()) as c:
        _covers(c, tmp_path)


def _covers(c, tmp_path):
    assert c.get("/api/music/covers").json() == {"covers": []}
    (tmp_path / "music_covers").mkdir()
    (tmp_path / "music_covers" / "cover_whale.png").write_bytes(PNG)
    covers = c.get("/api/music/covers").json()["covers"]
    assert covers == [{"name": "whale", "url": "/api/music/covers/cover_whale.png"}]
    assert c.get(covers[0]["url"]).content == PNG
    assert c.get("/api/music/covers/..%2Fstandee.json").status_code == 404
    assert c.get("/api/music/covers/nope.png").status_code == 404
