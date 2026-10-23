"""生活引擎：按作息生成事件、可复现、开机补算、每日上限、成员之间的私聊、记忆材料。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.chat import ROOM, ChatHub
from roundtable.core.life import LAST_TICK_KEY, LifeEngine, pair_room
from roundtable.core.memory import MemoryKeeper
from roundtable.core.service import RoundtableService, ServiceError
from roundtable.core.storage.repository import iso

from .orchestrator.conftest import Env

NOW = datetime(2026, 10, 23, 22, 0, tzinfo=UTC)  # 星期五晚上


def engine(env: Env) -> LifeEngine:
    hub = ChatHub(env.rt)
    return LifeEngine(env.rt, hub, MemoryKeeper(env.rt, hub.everyone))


def life_env(**life) -> Env:
    rules = Env().config.roundtable.chat
    if not life:
        return Env()
    return Env(
        rt_update={"chat": rules.model_copy(update={"life": rules.life.model_copy(update=life)})}
    )


def life_calls(env: Env) -> list:
    return [c for c in env.fake.calls if "虚拟的公寓楼" in c.messages[0].content]


def test_plan_follows_schedule_and_is_reproducible():
    env = Env()
    life = engine(env)
    plans = life.plan(NOW - timedelta(hours=24), NOW)
    again = life.plan(NOW - timedelta(hours=24), NOW)
    assert [(p.at, p.kind, p.members, p.topic) for p in plans] == [
        (p.at, p.kind, p.members, p.topic) for p in again
    ]
    assert {p.at.replace(minute=0) for p in plans} <= {NOW - timedelta(hours=h) for h in range(24)}
    # 半夜大家都在睡觉：没有事件
    assert not [p for p in plans if p.at.hour in (2, 3, 4)]
    gathers = [p for p in plans if p.kind == "gather"]
    assert gathers and all(2 <= len(p.members) <= 3 and p.room == ROOM for p in gathers)
    assert all("时间：星期" in p.topic and "在场：" in p.topic for p in plans)


def test_plan_mentions_visible_office_work_only():
    env = Env()
    life = engine(env)
    rows = [
        {"model_id": m.model_id, "question": f"题目{i}", "anonymous": i == 1, "revealed_at": None}
        for i, m in enumerate(life.hub.everyone())
    ]
    env.rt.repo.office_work_between = lambda start, end: rows
    plans = life.plan(NOW - timedelta(hours=6), NOW)
    text = "\n".join(p.topic for p in plans)
    assert "题目0" in text
    assert "题目1" not in text  # 匿名未揭晓的会话不提


async def test_first_tick_does_not_backfill_and_records_last_tick():
    env = Env()
    life = engine(env)
    out = await life.tick(NOW)
    assert out["written"] <= env.config.roundtable.chat.life.max_written_per_tick
    assert env.rt.repo.setting(LAST_TICK_KEY) == NOW.isoformat()
    # 只推进了一个间隔：事件都在最近半小时里的整点
    events = env.rt.repo.life_events_between("2000", "3000")
    assert all(e["at"] >= (NOW - timedelta(minutes=30)).isoformat() for e in events)
    assert not any(e["offline"] for e in events)
    # 再推进到同一时刻什么也不做
    assert (await life.tick(NOW))["events"] == 0


async def test_catch_up_after_being_away_marks_offline_and_caps_written():
    env = Env()
    life = engine(env)
    env.rt.repo.set_setting(LAST_TICK_KEY, (NOW - timedelta(hours=10)).isoformat())
    out = await life.tick(NOW)
    cap = env.config.roundtable.chat.life.max_written_catch_up
    assert 1 <= out["written"] <= cap
    assert out["messages"] == len(life_calls(env))
    events = env.rt.repo.life_events_between("2000", "3000")
    assert all(e["offline"] for e in events)
    assert sum(e["written"] for e in events) == out["written"]
    written = [e for e in events if e["written"]]
    msgs = [
        m
        for e in written
        for m in env.rt.repo.chat_messages(e["room"], limit=50)
        if m["meta"] and m["meta"].get("life")
    ]
    assert msgs and all(m["meta"]["offline"] for m in msgs)
    # 补出来的消息按事件发生的时刻，不是现在
    assert all(m["created_at"] < NOW.isoformat() for m in msgs)
    # 生活只在生活群和成员之间的私聊里，不进用户的私聊
    assert {e["room"] for e in written} <= {ROOM} | {
        r["id"] for r in env.rt.repo.chat_rooms() if r["kind"] == "pair"
    }
    for c in life_calls(env):
        assert "<scene>" in c.messages[-1].content
    # 调用记在聊天的隐藏会话上，step = life
    assert life.spent_today() >= 0


async def test_catch_up_never_reaches_further_back_than_configured():
    env = life_env(catch_up_hours=3)
    life = engine(env)
    env.rt.repo.set_setting(LAST_TICK_KEY, (NOW - timedelta(days=5)).isoformat())
    await life.tick(NOW)
    events = env.rt.repo.life_events_between("2000", "3000")
    assert events and min(e["at"] for e in events) >= (NOW - timedelta(hours=3)).isoformat()


async def test_daily_limit_stops_calls_but_keeps_events():
    env = life_env(daily_usd=0.0)
    life = engine(env)
    env.rt.repo.set_setting(LAST_TICK_KEY, (NOW - timedelta(hours=10)).isoformat())
    out = await life.tick(NOW)
    assert out["events"] > 0 and out["written"] == 0
    assert not env.fake.calls  # 生活和记忆整理都不调用


async def test_disabled_does_nothing():
    env = life_env(enabled=False)
    out = await engine(env).tick(NOW)
    assert out == {"events": 0, "written": 0, "messages": 0, "memories": 0, "shares": 0}
    assert env.rt.repo.setting(LAST_TICK_KEY) is None


async def test_pair_room_is_view_only_for_user():
    env = Env()
    life = engine(env)
    room = pair_room("V2", "V1")
    assert room == "pair:V1+V2"
    plan = life.plan(NOW - timedelta(hours=1), NOW)[0]
    plan.kind, plan.members, plan.room = "pair", ["V1", "V2"], room
    assert await life.write(plan, offline=False, now=NOW) == env.config.roundtable.chat.life.turns
    info = env.rt.repo.chat_room(room)
    assert info["kind"] == "pair" and not info["with_user"] and info["title"] == "阿一 和 阿二"
    authors = [m["author"] for m in env.rt.repo.chat_messages(room, limit=10)]
    assert authors == ["V1", "V2", "V1"]

    service = RoundtableService(env.rt)
    rooms = service.chat_rooms()
    assert any(r["id"] == room and r["kind"] == "pair" for r in rooms["rooms"])
    assert rooms["life"]["enabled"] and rooms["life"]["daily_usd"] > 0
    with pytest.raises(ServiceError):
        await service.chat_post("我也来", room)
    assert service.chat_view(room=room)["room"]["kind"] == "pair"


async def test_unwritten_life_events_feed_the_day_memory():
    env = Env()
    life = engine(env)
    day = datetime(2026, 10, 21, tzinfo=UTC)
    plans = life.plan(day + timedelta(hours=8), day + timedelta(hours=21))
    for p in plans:
        env.rt.repo.add_life_event(iso(p.at), p.kind, p.members, place=p.place, topic=p.topic)
    member = life.hub.everyone()[0]
    log = life.memory.day_log(member, day.date(), UTC)
    assert "食堂" in log or "办公室" in log
    assert all(line[5:7] == " （" for line in log.splitlines())  # 「时:分 （地点）」
