"""习惯计数（M17）与技能熟练度（M18）：从全部经历累积；偶尔一次不算习惯；等级越往上越难升。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.member.growth import GrowthLog
from roundtable.core.member.growth.habits import (
    count,
    habits,
    keys_of,
    level,
    level_name,
    skills,
    slot,
    text,
)
from roundtable.core.storage.repository import iso

from ...orchestrator.conftest import Env
from .common import growth_env

START = datetime(2026, 9, 7, 23, 30, tzinfo=UTC)  # 周一深夜


def ev(etype, at, member="V1", **data):
    return {"kind": etype, "at": iso(at), "member": member, "data": data}


def test_slots_and_keys():
    assert [slot(h) for h in (0, 4, 5, 12, 15, 20, 23)] == [
        "深夜", "深夜", "早上", "中午", "下午", "晚上", "深夜",
    ]  # fmt: skip
    assert keys_of(ev("life.event", START, kind="gather", place="roof"), UTC, str.upper) == [
        "做什么:和大家聚在一起",
        "常去:ROOF",
    ]
    assert keys_of(ev("life.event", START, kind="pair", place="x"), UTC, str) == [
        "做什么:和人私下聊天"
    ]
    assert keys_of(ev("chat.message", START, from_user=False), UTC, str) == ["说话:深夜"]
    assert keys_of(ev("chat.message", START, from_user=True), UTC, str) == []
    assert keys_of(ev("work.done", START), UTC, str) == ["干活:在办公室干活"]
    assert keys_of(ev("perceive.video", START), UTC, str) == ["看过听过:video"]
    assert keys_of(ev("state.reflex", START), UTC, str) == []


def test_habit_needs_count_and_spread_over_weeks():
    rules = Env().config.roundtable.chat.engine.growth.habits
    blob: dict = {}
    burst = [ev("chat.message", START + timedelta(minutes=i)) for i in range(20)]
    count(blob, burst, UTC)
    assert blob["counts"]["说话:深夜"] == {"n": 20, "weeks": 1, "last": "2026-W37"}
    assert habits(blob, rules) == []  # 一周里说再多也不算习惯
    later = [ev("chat.message", START + timedelta(weeks=w)) for w in (1, 2)]
    count(blob, later, UTC)
    (h,) = habits(blob, rules)
    assert h["label"] == "常在深夜说话" and h["n"] == 22 and h["weeks"] == 3


def test_skill_levels_get_harder():
    assert [level(x) for x in (0, 1, 3, 7, 15, 1000, 10**9)] == [0, 1, 2, 3, 4, 9, 10]
    assert level_name(0) == "没怎么碰过" and level_name(5) == "熟练" and level_name(10) == "精通"
    sk = skills([{"domain": "学术", "items": 10, "hits": 21}], ["学术", "日常"])
    assert sk[0] == {"domain": "学术", "xp": 31, "level": 5, "name": "熟练", "next": 63}
    assert sk[1]["level"] == 0 and sk[1]["next"] == 1
    assert "学术 5 级（熟练）" in text([], sk) and text([], sk[1:]) == ""


def test_log_counts_from_cursor_and_clear_recounts_everything():
    env = growth_env()
    log = GrowthLog(env.rt)
    events = [ev("work.done", START + timedelta(days=i)) for i in range(5)]
    events += [ev("work.done", START, member="V2")]
    until = iso(START + timedelta(days=2))
    assert log.count("V1", events, until, UTC) == 2
    assert log.count("V1", events, iso(START + timedelta(days=10)), UTC) == 3  # 只读游标以后的
    assert log.load("V1")["counts"]["干活:在办公室干活"]["n"] == 5
    log.clear("V1")
    assert log.count("V1", events, iso(START + timedelta(days=10)), UTC) == 5  # 从头再算
