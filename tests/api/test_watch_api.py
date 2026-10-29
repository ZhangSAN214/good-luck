"""投屏陪伴接口：列来源、开始（要同意）、预览、聊天、安静看、停止；平板只能聊天和停止。"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import LanAccess, RoundtableService

from ..core.orchestrator.conftest import Env

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from roundtable.core.screen import FakeScreenSource, SourceInfo  # noqa: E402

PW = "home-" + "pass"
PC = ("127.0.0.1", 50000)
PAD = ("192.168.1.20", 50001)
BASE = "http://192.168.1.5:8000"


def env_with_screen() -> Env:
    env = Env()
    src = FakeScreenSource(
        [Image.new("RGB", (640, 360), (20, 20, 20))],
        sources=[SourceInfo("pc", "pc", "整个屏幕")],
    )
    env.rt.screen_sources = {"pc": src}
    return env


def wait_for(c, cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = c.get("/api/watch/messages").json()
        if cond(data):
            return data
        time.sleep(0.02)
    raise AssertionError("等不到")


def test_watch_round_trip():
    env = env_with_screen()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        st = c.get("/api/watch", params={"devices": True}).json()
        assert not st["running"] and st["devices"]["pc"]["sources"][0]["id"] == "pc"
        assert st["budget"] == {"spent_usd": 0, "limit_usd": 1.5}
        assert c.get("/api/watch/frame").status_code == 404
        # 不同意不能开始
        r = c.post("/api/watch/start", json={"source": "pc", "watchers": ["V1"]})
        assert r.status_code == 400 and "同意" in r.json()["detail"]
        bad = {"source": "nope", "watchers": ["V1"], "consent": True}
        assert c.post("/api/watch/start", json=bad).status_code == 400
        r = c.post("/api/watch/start", json={"source": "pc", "watchers": ["V1"], "consent": True})
        assert r.status_code == 200 and r.json()["running"]
        data = wait_for(c, lambda d: any(m["author"] == "V1" for m in d["messages"]))
        assert data["messages"][0]["author"] == "system"
        frame = c.get("/api/watch/frame")
        assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg"
        assert frame.headers["cache-control"] == "no-store"
        assert c.post("/api/watch/quiet", json={"quiet": True}).json()["quiet"] is True
        r = c.post("/api/watch/say", json={"text": "@阿一 你看这个"})
        assert r.status_code == 202 and r.json()["speakers"] == ["阿一"]
        wait_for(c, lambda d: d["messages"][-1]["author"] == "V1" and not d["typing"])
        stopped = c.post("/api/watch/stop").json()
        assert not stopped["running"]
        assert "停止了投屏" in c.get("/api/watch/messages").json()["messages"][-1]["text"]
        assert c.post("/api/watch/say", json={"text": "还在吗"}).status_code == 400
        # 一起看的会话不出现在群聊列表
        rooms = [r["id"] for r in c.get("/api/chat/rooms").json()["rooms"]]
        assert not any(r.startswith("watch:") for r in rooms)


def test_tablet_can_chat_and_stop_but_not_start_or_preview(tmp_path):
    env = env_with_screen()
    lan = LanAccess(tmp_path / "lan_access.json", enabled=True)
    app = create_app(RoundtableService(env.rt), lan=lan)
    pc = TestClient(app, client=PC, base_url="http://127.0.0.1:8000")
    pad = TestClient(app, client=PAD, base_url=BASE)
    with pc, pad:
        pc.post("/api/lan/password", json={"password": PW})
        assert pad.post("/api/lan/login", json={"password": PW}).status_code == 200
        body = {"source": "pc", "watchers": ["V1"], "consent": True}
        assert pad.post("/api/watch/start", json=body).status_code == 403
        assert pad.get("/api/watch", params={"devices": True}).status_code == 403
        assert pc.post("/api/watch/start", json=body).status_code == 200
        assert pad.get("/api/watch/frame").status_code == 403
        assert pad.get("/api/watch").json()["running"]
        assert pad.post("/api/watch/stop").json()["running"] is False
        pair = {"host": "192.168.1.5", "port": 41234, "code": "123456"}
        assert pad.post("/api/watch/adb/pair", json=pair).status_code == 403
        assert pad.post("/api/watch/adb/connect", json={}).status_code == 403


class FakeWirelessAdb:
    """只有配对 / 连接的假安卓来源。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def available(self):
        return None

    def sources(self):
        return []

    def pair(self, host, port, code):
        from roundtable.core.screen import ScreenUnavailable

        self.calls.append(("pair", host, port, code))
        if code != "123456":
            raise ScreenUnavailable("配对没成功")
        return "配对成功"

    def connect(self, host="", port=0):
        self.calls.append(("connect", host, port))
        return [f"{host or '192.168.1.5'}:{port or 37000}"]


def test_adb_pair_and_connect_from_the_pc():
    env = env_with_screen()
    adb = FakeWirelessAdb()
    env.rt.screen_sources["android"] = adb
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.post(
            "/api/watch/adb/pair", json={"host": "192.168.1.5", "port": 41234, "code": "123456"}
        )
        assert r.status_code == 200 and r.json() == {
            "message": "配对成功",
            "connected": ["192.168.1.5:37000"],
        }
        bad = c.post(
            "/api/watch/adb/pair", json={"host": "192.168.1.5", "port": 1, "code": "000000"}
        )
        assert bad.status_code == 400 and "配对没成功" in bad.json()["detail"]
        assert (
            c.post("/api/watch/adb/pair", json={"host": "x", "port": 1, "code": "1"}).status_code
            == 422
        )
        r = c.post("/api/watch/adb/connect", json={"host": "192.168.1.5", "port": 37001})
        assert r.json() == {"connected": ["192.168.1.5:37001"]}
