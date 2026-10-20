"""公寓楼：作息、上班的人、匿名会议不暴露是谁。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.office import building, schedule
from roundtable.core.storage import Repository, connect

from ..routing.conftest import app_config

MON, SAT = 0, 5


def by_id(b):
    return {r.id: r for r in b.residents}


def test_schedule_follows_the_clock():
    for rid in ("V1", "V2", "V3"):
        assert schedule(rid, 3, MON) == ("dorm", "sleep")
        assert schedule(rid, 10, MON) == ("office", "idle")
        assert schedule(rid, 12, MON) == ("canteen", "eat")
        assert schedule(rid, 15, SAT)[0] != "office"
        assert schedule(rid, 22, MON) == ("dorm", "read")
    # 同一时刻结果不变
    assert schedule("V1", 20, 2) == schedule("V1", 20, 2)


def test_one_resident_per_vendor_and_floors():
    cfg = app_config()
    b = building(cfg, hour=10, weekday=MON)
    people = by_id(b)
    assert set(people) == {"V1", "V2", "V3", "V4", "V5", "V6"}
    assert people["V1"].model_ids == ("b1", "f1") and people["V1"].role == "manager"
    assert people["V3"].role == "staff"
    assert people["V1"].room == "office" and people["V1"].activity_label == "待命"
    kinds = [[r["kind"] for r in floor] for floor in b.floors]
    assert kinds[0] == ["rooftop"] and kinds[-1] == ["canteen", "lounge", "gym"]
    assert kinds[-2] == ["office", "meeting"]
    dorms = [r["id"] for floor in b.floors for r in floor if r["kind"] == "dorm"]
    assert sorted(dorms) == sorted(r.dorm for r in b.residents)
    assert len(b.floors) == 5  # 天台 + 两层宿舍 + 办公层 + 一楼


def test_sleeping_residents_are_in_their_own_dorm():
    b = building(app_config(), hour=3, weekday=MON)
    assert all(r.room == r.dorm and r.activity == "sleep" for r in b.residents)


def test_working_and_waiting_and_hidden_sessions():
    def seat(sid, model, status="running", workflow="office", hidden=False):
        return {
            "session_id": sid,
            "model_id": model,
            "status": status,
            "workflow": workflow,
            "hidden": hidden,
        }

    working = [
        seat("s1", "b1", workflow="discussion"),
        seat("s2", "b2", status="awaiting_confirmation"),
        seat("s3", "b3", hidden=True),
    ]
    b = building(app_config(), hour=3, weekday=MON, working=working)
    people = by_id(b)
    assert (people["V1"].room, people["V1"].activity, people["V1"].session_id) == (
        "meeting",
        "work",
        "s1",
    )
    assert (people["V2"].room, people["V2"].activity) == ("office", "wait")
    # 匿名未揭晓：只计数，住户照常睡觉，会话 id 不出现
    assert b.hidden_meetings == 1
    assert people["V3"].activity == "sleep" and people["V3"].session_id is None
    assert all(r.session_id != "s3" for r in b.residents)


def test_month_wages_add_up_per_resident():
    usage = {"b1": {"cost_usd": 0.25}, "f1": {"cost_usd": 1.0}, "b2": {"cost_usd": 0.5}}
    people = by_id(building(app_config(), hour=10, weekday=MON, month_usage=usage))
    assert people["V1"].month_paid_usd == 1.25 and people["V2"].month_paid_usd == 0.5


def test_repository_working_seats():
    now = [datetime(2026, 10, 20, 12, tzinfo=UTC)]
    repo = Repository(connect(":memory:"), clock=lambda: now[0].isoformat())
    open_ = repo.create_session("q", seed=1, anonymous=False)
    secret = repo.create_session("q", seed=2, anonymous=True)
    done = repo.create_session("q", seed=3, anonymous=False)
    for sid, model in ((open_, "b1"), (secret, "b2"), (done, "b3")):
        repo.add_seats(sid, 0, {"甲": model})
    repo.set_status(open_, "running")
    repo.set_status(secret, "running")
    repo.set_status(done, "completed")
    rows = repo.working_seats(now[0] - timedelta(hours=3))
    assert {(r["model_id"], r["hidden"]) for r in rows} == {("b1", False), ("b2", True)}
    # 太久没更新的不算
    assert repo.working_seats(now[0] + timedelta(minutes=1)) == []
