"""收得住：没新内容就收尾、每日条数上限、连续出错或预算告急当天停、每周自检。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ROOM
from roundtable.core.life import LAST_TICK_KEY
from roundtable.core.life_guard import HALT_KEY, week_key, week_window
from roundtable.core.providers.errors import ErrorKind
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from .orchestrator.conftest import Env
from .test_life import NOW, chatty, engine, life_calls, life_env


def guard_env(**guard) -> Env:
    rules = Env().config.roundtable.chat
    life = rules.life.model_copy(update={"guard": rules.life.guard.model_copy(update=guard)})
    return Env(rt_update={"chat": rules.model_copy(update={"life": life})})


def at(env: Env, moment: datetime) -> None:
    env.rt.budget.now = lambda: moment
    env.rt.repo.clock = lambda: iso(moment)


def plan_for(life, members=("V1", "V2")):
    plan = life.plan(NOW - timedelta(hours=1), NOW)[0]
    plan.kind, plan.members, plan.room = "gather", list(members), ROOM
    return plan


async def test_repeating_line_ends_the_conversation_and_is_not_posted():
    env = Env()  # 默认的假模型句句很像
    at(env, NOW)
    life = engine(env)
    said = await life.write(plan_for(life), offline=False, now=NOW)
    assert said == 1
    assert len(life_calls(env)) == 2  # 第二句太像：不发，到此收尾
    log = env.rt.repo.guard_log_between("2000", "3000")
    assert [r["kind"] for r in log] == ["stale"] and log[0]["member"] == "V2"


async def test_line_too_similar_to_recent_chat_in_room_is_dropped():
    env = chatty(Env())
    at(env, NOW)
    life = engine(env)
    first = await life.write(plan_for(life), offline=False, now=NOW)
    assert first == env.config.roundtable.chat.life.turns
    # 新的一段又说了和刚才一样的话
    old = env.rt.repo.chat_messages(ROOM)[0]["text"]
    env.fake._default = lambda model, messages: old
    assert await life.write(plan_for(life), offline=False, now=NOW) == 0


async def test_lines_are_clipped_to_max_chars():
    env = guard_env(max_chars=30)
    at(env, NOW)
    env.fake._default = lambda model, messages: "长" * 100
    life = engine(env)
    await life.write(plan_for(life), offline=False, now=NOW)
    text = env.rt.repo.chat_messages(ROOM)[0]["text"]
    assert text == "长" * 30 + "…"


async def test_daily_message_cap_stops_talking_and_logs_once():
    env = chatty(guard_env(daily_messages=2))
    at(env, NOW)
    life = engine(env)
    assert await life.write(plan_for(life), offline=False, now=NOW) == 2
    assert await life.write(plan_for(life), offline=False, now=NOW) == 0
    env.rt.repo.set_setting(LAST_TICK_KEY, (NOW - timedelta(hours=5)).isoformat())
    out = await life.tick(NOW)
    assert out["written"] == 0 and out["events"] > 0  # 事件照记
    log = env.rt.repo.guard_log_between("2000", "3000")
    assert [r["kind"] for r in log].count("cap") == 1
    assert life.guard.room_left() == 0


async def test_consecutive_failures_halt_the_day_and_resume_tomorrow():
    env = guard_env(max_failures=2)
    at(env, NOW)
    env.fake._default = lambda model, messages: (
        ErrorKind.SERVER if "现在你正和" in messages[0].content else "好"
    )
    life = engine(env)
    env.rt.repo.set_setting(LAST_TICK_KEY, (NOW - timedelta(hours=10)).isoformat())
    await life.tick(NOW)
    # 第二次出错后当天停掉，后面的事件不再调用
    day = (NOW - timedelta(days=1), NOW + timedelta(days=1))
    failed = env.rt.repo.recent_call_errors("chat", ("life",), *day, 50)
    assert failed == [True, True]
    reason = life.guard.halted()
    assert reason and "连续 2 次" in reason
    assert not life.allowed(0.0)
    calls = len(env.fake.calls)
    await life.tick(NOW + timedelta(hours=1))
    assert len(env.fake.calls) == calls  # 当天什么都不调用（含记忆整理）
    # 第二天自动恢复
    tomorrow = NOW + timedelta(days=1)
    at(env, tomorrow)
    assert life.guard.halted() is None
    assert life.allowed(0.0)


async def test_budget_alarm_halts_life():
    env = life_env(daily_usd=0.01)
    at(env, NOW)
    life = engine(env)
    life.spent_today = lambda: 0.0095  # 用到上限的 95%
    env.rt.repo.set_setting(LAST_TICK_KEY, (NOW - timedelta(hours=3)).isoformat())
    out = await life.tick(NOW)
    assert out["written"] == 0 and not life_calls(env)
    assert "接近每日上限" in life.guard.halted()


async def test_guard_disabled_keeps_old_behaviour():
    env = guard_env(enabled=False)
    at(env, NOW)
    life = engine(env)
    assert await life.write(plan_for(life), offline=False, now=NOW) == 3
    env.rt.repo.set_setting(HALT_KEY, '{"day": "2026-10-23", "reason": "x"}')
    assert life.guard.halted() is None
    assert not env.rt.repo.guard_log_between("2000", "3000")


def test_week_helpers():
    monday = datetime(2026, 10, 19, tzinfo=UTC)
    assert week_key(NOW) == "2026-W43"
    assert week_window(NOW) == (monday, monday + timedelta(days=7))


async def test_weekly_report_counts_cost_speakers_repeats_and_halts():
    env = Env()
    life = engine(env)
    repo = env.rt.repo
    week = datetime(2026, 10, 20, 12, tzinfo=UTC)  # 上一周（W43）的周二
    at(env, week)
    for text, who in [
        ("今天食堂的面真不错", "V1"),
        ("今天食堂的面真不错呀", "V2"),
        ("下雨了", "V1"),
    ]:
        repo.add_chat_message(ROOM, who, text, meta={"life": True})
    repo.add_chat_message("dm:V1", "V1", "收到")
    repo.add_chat_message("dm:V1", "user", "你好")
    life.guard.halt("连续 3 次调用出错")
    life.guard.note_stale("V2", "x")
    names = {m.id: m.name for m in life.hub.everyone()}

    # 每周一起第一次推进时写上一周的，只写一次：周日（W43）写的是 W42，下周一写 W43
    assert life.guard.weekly(datetime(2026, 10, 25, 20, tzinfo=UTC), names)["week"] == "2026-W42"
    monday = datetime(2026, 10, 26, 9, tzinfo=UTC)
    at(env, monday)
    report = life.guard.weekly(monday, names)
    assert report and report["week"] == "2026-W43"
    assert life.guard.weekly(monday, names) is None
    speakers = {s["member"]: s for s in report["speakers"]}
    assert speakers["V1"]["life"] == 2 and speakers["V1"]["replies"] == 1
    assert speakers["V1"]["name"] == "阿一"
    assert "user" not in speakers
    assert report["repeats"]["count"] == 1
    assert report["repeats"]["examples"][0]["name"] == "阿二"
    assert report["guard"]["stale"] == 1 and len(report["guard"]["halts"]) == 1
    assert any("熔断" in n for n in report["notes"])
    saved = repo.life_reports()
    assert saved[0]["week"] == "2026-W43" and saved[0]["repeats"]["count"] == 1


async def test_tick_writes_weekly_report_and_service_shows_it():
    env = chatty(Env())
    monday = datetime(2026, 10, 26, 9, tzinfo=UTC)
    at(env, monday)
    life = engine(env)
    out = await life.tick(monday)
    assert out["reports"] == 1
    assert (await life.tick(monday + timedelta(hours=1)))["reports"] == 0

    service = RoundtableService(env.rt)
    data = service.life_reports()
    assert data["reports"][0]["week"] == "2026-W43"
    assert data["recent"]["notes"] and data["halted"] is None
    rooms = service.chat_rooms()["life"]
    assert rooms["daily_messages"] == 40 and rooms["halted"] is None
    assert rooms["messages_today"] >= 0
