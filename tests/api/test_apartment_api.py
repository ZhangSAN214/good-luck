"""公寓楼接口：按浏览器本地时间给出住户位置；匿名未揭晓的会话不暴露是谁。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import RoundtableService

from ..core.orchestrator.conftest import Env


def test_apartment_endpoint():
    env = Env()
    repo = env.rt.repo
    secret = repo.create_session("q", seed=1, anonymous=True)
    repo.add_seats(secret, 0, {"甲": "b1"})
    repo.set_status(secret, "running")
    with TestClient(create_app(RoundtableService(env.rt))) as c:
        r = c.get("/api/apartment", params={"hour": 3, "weekday": 1})
        assert r.status_code == 200
        data = r.json()
        assert data["hidden_meetings"] == 1
        assert all(p["activity"] == "sleep" for p in data["residents"])
        assert secret not in r.text
        assert c.get("/api/apartment", params={"hour": 24, "weekday": 1}).status_code == 400
        assert c.get("/api/apartment").status_code == 422


def test_office_meeting_attendees_are_in_the_meeting_room():
    env = Env()
    repo = env.rt.repo
    sid = repo.create_session("q", seed=1, anonymous=False, workflow="office")
    repo.create_table(
        sid,
        0,
        plan=None,
        pipeline=["intake", "handle", "check", "reveal"],
        members={"甲": "b2", "乙": "b3"},
        coordinator="f1",
        escalate_to=None,
        estimate={},
        status="approved",
    )
    repo.add_seats(sid, 0, {"甲": "b2", "乙": "b3"}, "f1")
    repo.save_output(
        sid,
        table_no=0,
        step="intake",
        kind="office_intake",
        content={"mode": "meeting", "attendees": ["甲"], "reply": "开个会"},
    )
    repo.set_status(sid, "running")
    data = RoundtableService(env.rt).apartment(3, 1)
    rooms = {p["id"]: p["room"] for p in data["residents"] if p["session_id"] == sid}
    # 经理（V1）和参会的甲（V2）在会议室，没参会的乙（V3）在办公室
    assert rooms == {"V1": "meeting", "V2": "meeting", "V3": "office"}
