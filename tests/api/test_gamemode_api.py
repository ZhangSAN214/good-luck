"""游戏模式接口：读状态、手动开关、补跑一轮。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_get_and_toggle_game_mode():
    env = Env()
    svc = RoundtableService(env.rt)
    with TestClient(create_app(svc)) as c:
        d = c.get("/api/gamemode").json()
        assert d["busy"] is False and d["gpu_free"] is True and d["queue"] == []
        d = c.put("/api/gamemode", json={"manual": True}).json()
        assert d["manual"] and d["busy"] and d["reasons"] == ["你手动开了游戏模式"]
        assert env.rt.router.gpu_blocked("c") is None  # 假渠道不是显卡渠道
        assert c.get("/api/gamemode").json()["manual"] is True
        d = c.put("/api/gamemode", json={"manual": False}).json()
        assert d["manual"] is False and d["busy"] is False
        assert d["gpu_free"] is False  # 刚关：要连续空闲几分钟
        assert c.put("/api/gamemode", json={"manual": 1, "x": 2}).status_code == 422


async def test_tick_polls_and_drains():
    env = Env()
    svc = RoundtableService(env.rt)
    gm = env.rt.gamemode
    assert gm is not None
    ran = []

    async def job(payload):
        ran.append(payload)

    gm.register("t", job)
    gm.defer("t", "测试", {"n": 1})
    d = await svc.gamemode_tick()
    assert d["ran"] is True and ran == [{"n": 1}] and d["queue"] == []
