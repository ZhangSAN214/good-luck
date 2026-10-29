"""心跳与成员引擎：没人登记时不花钱；醒着的人才问；按优先级做事、超额降级只记一行；把生活和聊天转成事件。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.member.bus import EventBus
from roundtable.core.member.engine import LAST_KEY, MemberEngine
from roundtable.core.member.heartbeat import Heartbeat, Intent
from roundtable.core.member.scheduler import BudgetScheduler, Priority
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import Env

DAY = datetime(2026, 10, 29, 15, tzinfo=UTC)  # 下午：大家都醒着
NIGHT = datetime(2026, 10, 29, 3, tzinfo=UTC)  # 半夜：都在睡觉


def make(env: Env) -> Heartbeat:
    env.rt.budget.now = lambda: DAY
    bus = EventBus(env.rt)
    return Heartbeat(env.rt, bus, BudgetScheduler(env.rt))


async def test_no_wants_means_nothing_happens():
    env = Env()
    hb = make(env)
    out = await hb.beat(["V1", "V2"], DAY)
    assert out == {"awake": 2, "intents": 0, "done": 0, "noted": 0}
    assert not env.fake.calls


async def test_sleeping_members_are_not_asked():
    env = Env()
    hb = make(env)
    asked = []
    hb.register("m", lambda member, now: asked.append(member))
    out = await hb.beat(["V1", "V2"], NIGHT)
    assert out["awake"] == 0 and asked == []


async def test_intents_run_by_priority_and_extra_are_noted():
    env = Env()
    hb = make(env)
    ran: list[str] = []

    def want(member, now):
        async def run():
            ran.append(member)

        prio = Priority.USER if member == "V2" else Priority.LIFE
        return Intent(member, prio, 0.0001, run, note=f"{member} 想聊天")

    hb.register("talk", want)
    out = await hb.beat(["V1", "V2", "V3", "V4"], DAY)
    assert ran[0] == "V2"  # 优先级高的先做
    assert out["done"] == min(4, env.config.roundtable.chat.engine.heartbeat.max_actions)
    assert out["done"] + out["noted"] == 4
    notes = hb.bus.replay(DAY - timedelta(minutes=1), DAY + timedelta(minutes=1), "engine.note")
    assert len(notes) == out["noted"]
    beats = hb.bus.replay(DAY - timedelta(minutes=1), DAY + timedelta(minutes=1), "heartbeat")
    assert beats[0].data == out


async def test_over_budget_intent_is_degraded_to_a_note():
    env = Env()
    hb = make(env)
    ran = []

    async def run():
        ran.append(1)

    hb.register("big", lambda m, now: Intent(m, Priority.LIFE, 99.0, run, note="想发朋友圈"))
    out = await hb.beat(["V1"], DAY)
    assert ran == [] and out["noted"] == 1


async def test_broken_module_does_not_stop_heartbeat():
    env = Env()
    hb = make(env)

    def broken(member, now):
        raise RuntimeError("坏")

    hb.register("broken", broken)
    assert (await hb.beat(["V1"], DAY))["intents"] == 0


async def test_engine_bridges_life_and_chat_into_events():
    env = Env()
    env.rt.budget.now = lambda: DAY
    repo = env.rt.repo
    engine = MemberEngine(env.rt, lambda: ["V1", "V2"])
    repo.set_setting(LAST_KEY, (DAY - timedelta(hours=1)).isoformat())
    repo.add_life_event(iso(DAY - timedelta(minutes=30)), "gather", ["V1", "V2"], place="canteen")
    repo.clock = lambda: iso(DAY - timedelta(minutes=10))
    repo.add_chat_message("main", "user", "大家好")
    seen = []
    engine.bus.subscribe("*", lambda e: seen.append((e.kind, e.member)))
    out = await engine.tick(DAY)
    assert out["events"] == 3 and out["awake"] == 2 and out["intents"] == 0
    assert ("life.event", "V1") in seen and ("life.event", "V2") in seen
    assert ("chat.message", None) in seen and ("heartbeat", None) in seen
    assert repo.setting(LAST_KEY) == DAY.isoformat()
    # 再推进到同一时刻：没有新事件
    assert (await engine.tick(DAY))["events"] == 0
    assert not env.fake.calls


def test_engine_context_uses_configured_budget():
    from roundtable.core.member.context import Piece

    env = Env()
    engine = MemberEngine(env.rt, lambda: [])
    ctx = engine.context([Piece("memory", "字" * 2000, 1)])
    assert ctx.chars <= env.config.roundtable.chat.engine.context.budget_chars
