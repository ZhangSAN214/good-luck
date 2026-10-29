"""日历接口：近几天的日子、加 / 删你的纪念日、参数错误给 400。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_calendar_view_and_user_dates():
    env = Env()
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.get("/api/calendar", params={"start": "2026-12-24", "days": 3})
        assert r.status_code == 200
        days = r.json()["days"]
        assert [d["date"] for d in days] == ["2026-12-24", "2026-12-25", "2026-12-26"]
        assert days[1]["rest"] and days[1]["items"][0]["name"] == "圣诞节"
        assert days[0]["lunar"] and days[0]["weekday"] == "周四"
        assert len(c.get("/api/calendar").json()["days"]) == 7  # 默认 board_days

        r = c.post("/api/calendar/dates", json={"name": "来曼城的日子", "date": "2025-12-25"})
        assert r.status_code == 200
        did = r.json()["date"]["id"]
        day = c.get("/api/calendar", params={"start": "2026-12-25", "days": 1}).json()["days"][0]
        assert any(i["name"] == "来曼城的日子" and i["years"] == 1 for i in day["items"])

        assert c.post("/api/calendar/dates", json={"name": "x", "date": "12/25"}).status_code == 400
        assert c.delete(f"/api/calendar/dates/{did}").json()["user_dates"] == []
        assert c.delete(f"/api/calendar/dates/{did}").status_code == 404
        assert c.get("/api/calendar", params={"start": "bad"}).status_code == 400
        assert c.get("/api/calendar", params={"days": 999}).status_code == 400


async def test_life_tick_publishes_calendar_events():
    env = Env()
    svc = RoundtableService(env.rt)
    await svc.life_tick()
    kinds = [e["kind"] for e in env.rt.repo.member_events_between("2000-01-01", "2100-01-01")]
    assert kinds.count("calendar.day") == 1
    await svc.life_tick()
    kinds = [e["kind"] for e in env.rt.repo.member_events_between("2000-01-01", "2100-01-01")]
    assert kinds.count("calendar.day") == 1  # 一天只发一次
