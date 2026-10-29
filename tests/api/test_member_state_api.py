"""状态接口：看每位成员此刻的心情和精力（只读），一键清空回到基线。"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.member.instinct import Stimulus
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env

NOON = datetime(2026, 10, 29, 12, tzinfo=UTC)


def test_state_api_reads_and_clears():
    env = Env()
    env.rt.budget.now = lambda: NOON
    svc = RoundtableService(env.rt)
    ids = [m.id for m in svc.chats.everyone()]
    svc.engine.state.feel(Stimulus(ids[0], NOON, frozenset({"from_user", "dm", "criticized"})))
    with TestClient(create_app(svc)) as c:
        rows = {r["member"]: r for r in c.get("/api/chat/engine/state").json()["members"]}
        assert set(rows) == set(ids)
        assert rows[ids[0]]["mood"] < 0 and rows[ids[1]]["mood"] == 0
        assert rows[ids[0]]["recent"][-1]["name"] == "被批评"
        assert 0 <= rows[ids[1]]["energy"] <= 1 and rows[ids[1]]["line"]
        assert c.delete("/api/chat/engine/state").json()["deleted"] == 1
        rows = {r["member"]: r for r in c.get("/api/chat/engine/state").json()["members"]}
        assert rows[ids[0]]["mood"] == 0
