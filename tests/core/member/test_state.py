"""P3 状态：心情和精力全由程序按事件算，快慢两层、回到基线；基线读人物卡第 5、8 章；
本能反射走规则表（接口可换）；状态只进生活提示词，不进工作模式；只有状态插件写状态。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roundtable.core.member import WorkModeError
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.engine import LAST_KEY, MemberEngine
from roundtable.core.member.instinct import Reflex, Stimulus
from roundtable.core.member.state import (
    REFLEX,
    Baseline,
    StatePlugin,
    circadian,
    derive_baseline,
    times_in,
)
from roundtable.core.persona import chat_persona
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import MEDIUM, Env

NOON = datetime(2026, 10, 29, 12, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[3] / "src" / "roundtable"

EARLY_DAILY = """### 工作日

| 时间 | 做什么 |
|---|---|
| 6:30 | 起床，整理床铺，泡茶 |
| 12:00 | 午饭 |
| 22:30 | 洗漱，睡前写小本子 |

### 睡觉习惯

不晚于十一点。睡前必须把桌上的东西归位。"""

OWL_DAILY = """**工作日**：

- **8:20—9:00**：闹钟通常要响两轮。
- **午后**：尽量留二十分钟短觉。
- **21:30 以后**：反而容易精神，读书、写批注。尽量在半夜前睡，月色特别好时允许自己晚一点。

**休息日**：通常睡到十点左右。"""


def profile(scores=(3, 3, 3, 3, 3), feel="", daily=""):
    traits = dict(zip(("外向", "较真", "好奇", "温和", "幽默"), scores, strict=True))
    return {"personality": {"五项": traits, "情绪": feel}, "state": {"日常": daily}}


def plugin(env: Env, **kw) -> StatePlugin:
    env.rt.budget.now = lambda: NOON
    return StatePlugin(env.rt, tz=UTC, **kw)


def members(env: Env) -> list[str]:
    return [m.id for m in RoundtableService(env.rt).chats.everyone()]


def stim(member, at, *tags):
    return Stimulus(member, at, frozenset(tags))


# --- 基线 --------------------------------------------------------------------------


def test_times_in_reads_digits_and_chinese():
    assert times_in("6:30 起床") == [6.5]
    assert times_in("不晚于十一点半") == [11.5]
    assert times_in("尽量在半夜前睡") == [24.0]
    assert times_in("允许自己晚一点，趴一点，一点点就好") == []
    assert times_in("凌晨一点才睡") == [1.0]


def test_baseline_reads_daily_chapter():
    rules = Env().rt.config.roundtable.chat.engine.state.baseline
    early = derive_baseline(profile(daily=EARLY_DAILY), "x", rules)
    assert (early.wake, early.sleep, early.night_owl) == (6.5, 23.0, False)
    owl = derive_baseline(profile(daily=OWL_DAILY), "x", rules)
    assert owl.wake == pytest.approx(8 + 20 / 60) and owl.sleep == 24.0 and owl.night_owl


def test_baseline_from_traits_and_emotion_chapter():
    rules = Env().rt.config.roundtable.chat.engine.state.baseline
    plain = derive_baseline(profile(), "x", rules)
    assert plain.mood == 0 and plain.sensitivity == 1
    sunny = derive_baseline(profile((5, 3, 3, 4, 5), feel="我很乐观，想得开"), "x", rules)
    touchy = derive_baseline(profile((2, 5, 3, 2, 2), feel="容易内耗，想太多"), "x", rules)
    calm = derive_baseline(profile((3, 3, 3, 5, 3), feel="我很少生气，比较淡定"), "x", rules)
    assert sunny.mood > plain.mood > touchy.mood
    assert touchy.sensitivity > plain.sensitivity > calm.sensitivity


def test_no_profile_uses_apartment_hours():
    env = Env()
    for m in members(env):
        b = plugin(env).baseline(m)
        assert b.mood == 0 and b.sensitivity == 1 and b.sleep - b.wake >= 12


def test_circadian_low_at_night_peak_by_day_and_owl_stays_up():
    c = Env().rt.config.roundtable.chat.engine.state.circadian
    early = Baseline(wake=6.5, sleep=23)
    assert circadian(3, early, c) == c.asleep
    assert circadian(10, early, c) == pytest.approx(c.peak)
    assert circadian(22.5, early, c) < circadian(10, early, c)
    assert circadian(14, early, c) < circadian(10, early, c)  # 午后低谷
    owl = Baseline(wake=8.5, sleep=24.5, night_owl=True)
    assert circadian(23.5, owl, c) > circadian(23.5, Baseline(wake=8.5, sleep=24.5), c)
    assert circadian(0.2, owl, c) > c.asleep  # 过了半夜还醒着


# --- 快慢两层、回到基线 ---------------------------------------------------------------


def test_criticism_drops_mood_then_recovers_to_baseline():
    env = Env()
    m = members(env)[0]
    st = plugin(env)
    assert st.mood_of(m, NOON) == 0
    got = st.feel(stim(m, NOON, "from_user", "dm", "criticized"))
    assert "被批评" in [r.name for r in got]
    right_after = st.mood_of(m, NOON)
    assert right_after < -0.2
    later = st.mood_of(m, NOON + timedelta(hours=12))
    assert right_after < later < 0  # 快层消退了，慢层的底色还在
    assert st.mood_of(m, NOON + timedelta(days=14)) == pytest.approx(0, abs=0.01)
    assert st.read(m, NOON).layers["mood_slow"] < 0


def test_impulses_commute_out_of_order():
    env = Env()
    a, b = members(env)[:2]
    st = plugin(env)
    t1, t2 = NOON, NOON + timedelta(hours=3)
    st.feel(stim(a, t1, "from_user", "dm", "criticized"))
    st.feel(stim(a, t2, "from_user", "dm", "praised"))
    st.feel(stim(b, t2, "from_user", "dm", "praised"))
    st.feel(stim(b, t1, "from_user", "dm", "criticized"))  # 补算时晚到
    at = t2 + timedelta(hours=1)
    assert st.mood_of(a, at) == pytest.approx(st.mood_of(b, at), abs=1e-9)


def test_sensitivity_scales_mood_impulses():
    env = Env()
    touchy, calm = members(env)[:2]
    st = plugin(env)
    st.profiles.change(touchy, {"personality": {"五项": {"较真": 5, "温和": 1}}}, author="user")
    st.profiles.change(calm, {"personality": {"五项": {"较真": 1, "温和": 5}}}, author="user")
    for m in (touchy, calm):
        st.feel(stim(m, NOON, "from_user", "dm", "criticized"))
    assert st.mood_of(touchy, NOON) < st.mood_of(calm, NOON) < 0


def test_talking_drains_energy_and_rest_returns():
    env = Env()
    m = members(env)[0]
    st = plugin(env)
    before = st.energy_of(m, NOON)
    for k in range(20):
        st.feel(stim(m, NOON + timedelta(minutes=k), "spoke"))
    tired = st.energy_of(m, NOON + timedelta(minutes=20))
    assert tired < before - 0.2
    assert st.energy_of(m, NOON + timedelta(hours=30)) == pytest.approx(
        circadian(18, st.baseline(m), st.rules.circadian), abs=0.05
    )


# --- 本能反射 ----------------------------------------------------------------------


def test_night_message_makes_her_sleepy():
    env = Env()
    m = members(env)[0]
    st = plugin(env)
    night = datetime(2026, 10, 29, 3, tzinfo=UTC)
    acts = [r.act for r in st.feel(stim(m, night, "from_user", "dm"))]
    assert "sleepy" in acts and "reply_now" in acts
    day = [r.act for r in st.feel(stim(m, NOON, "from_user", "dm"))]
    assert "sleepy" not in day


def test_group_mention_targets_only_named_member_and_publishes_reflex():
    env = Env()
    svc = RoundtableService(env.rt)
    env.rt.budget.now = lambda: NOON
    everyone = svc.chats.everyone()
    a, b = everyone[0], everyone[1]
    st = svc.engine.state
    st.tz = UTC
    reflexes = []
    svc.engine.bus.subscribe(REFLEX, reflexes.append)
    msg = env.rt.repo.add_chat_message(
        "main", "user", f"@{a.name} 这不对吧，你搞错了", created_at=iso(NOON)
    )
    svc.engine.bus.publish(
        Event("chat.message", NOON, None, {"room": "main", "id": msg["id"], "from_user": True})
    )
    assert st.mood_of(a.id, NOON) < 0 and st.mood_of(b.id, NOON) == 0
    names = {(e.member, e.data["name"]) for e in reflexes}
    assert {(a.id, "被点名"), (a.id, "被批评")} <= names
    assert all(e.member == a.id for e in reflexes)
    assert next(e for e in reflexes if e.data["name"] == "被点名").data["act"] == "reply_now"


def test_everyone_criticized_is_milder_than_direct():
    env = Env()
    svc = RoundtableService(env.rt)
    env.rt.budget.now = lambda: NOON
    st = svc.engine.state
    st.tz = UTC
    msg = env.rt.repo.add_chat_message("main", "user", "大家都搞错了", created_at=iso(NOON))
    svc.engine.bus.publish(
        Event("chat.message", NOON, None, {"room": "main", "id": msg["id"], "from_user": True})
    )
    ids = [m.id for m in svc.chats.everyone()]
    group = st.mood_of(ids[0], NOON)
    assert all(st.mood_of(m, NOON) == pytest.approx(group) for m in ids)
    direct = plugin(env).feel(
        stim(ids[0], NOON + timedelta(seconds=1), "from_user", "dm", "criticized")
    )
    assert direct and -0.2 < group < 0


def test_instinct_is_swappable():
    class Net:  # 以后的小网络：同一个接口
        def react(self, s):
            return [Reflex("网", mood=0.5, act="smile")] if "life" in s.tags else []

    env = Env()
    m = members(env)[0]
    bus = EventBus(env.rt)
    st = plugin(env, bus=bus, instinct=Net())
    bus.publish(Event("life.event", NOON, m, {"kind": "gather"}))
    assert st.mood_of(m, NOON) > 0.3
    assert st.read(m, NOON).recent[-1]["act"] == "smile"


async def test_engine_observe_counts_user_message_before_replies():
    env = Env()
    svc = RoundtableService(env.rt)
    env.rt.budget.now = lambda: NOON
    m = svc.chats.everyone()[0]
    env.rt.repo.set_setting(LAST_KEY, (NOON - timedelta(minutes=5)).isoformat())
    env.rt.repo.add_chat_message(f"dm:{m.id}", "user", "你太敷衍了", created_at=iso(NOON))
    assert svc.engine.observe(NOON) >= 1
    assert svc.engine.state.mood_of(m.id, NOON) < 0
    assert svc.engine.observe(NOON) == 0  # 不重复算


# --- 进提示词：只在生活模式，8 位相同 ---------------------------------------------------


def test_state_line_same_budget_for_everyone_and_refuses_work_mode():
    env = Env()
    st = plugin(env)
    ids = members(env)
    st.feel(stim(ids[0], NOON, "from_user", "dm", "criticized"))
    lines = [st.line(m, NOON) for m in ids]
    assert all(0 < len(x) <= st.rules.context_chars for x in lines)
    assert "不痛快" in lines[0] and "不痛快" not in lines[1]
    with pytest.raises(WorkModeError):
        st.line(ids[0], NOON, mode="work")


def test_chat_persona_puts_state_after_card():
    env = Env()
    env.rt.budget.now = lambda: NOON
    m = members(env)[0]
    st = plugin(env)
    st.profiles.change(
        m, {"identity": {"名字": "甲"}, "personality": {"五项": {"外向": 3}}}, author="user"
    )
    calm = chat_persona(env.rt, m)
    st.feel(stim(m, NOON, "from_user", "dm", "criticized"))
    upset = chat_persona(env.rt, m)
    card = calm.split("\n")[0]
    assert upset.startswith(card) and calm != upset  # 随身卡这段前缀不变
    assert "不痛快" in upset
    assert len(upset) <= env.rt.config.roundtable.chat.engine.context.budget_chars


async def test_state_never_reaches_work_prompts():
    mark = "状态标记QW7"

    class Marker:
        def react(self, s):
            return [Reflex("标记", mood=-0.5, energy=-0.5, note=mark)]

    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    for m in svc.chats.everyone():
        StatePlugin(env.rt, instinct=Marker()).feel(stim(m.id, datetime.now(UTC), "life"))
        svc.engine.profiles.change(m.id, {"identity": {"名字": m.name}}, author="user")
        assert mark in chat_persona(env.rt, m.id)  # 生活里看得到
    sid = svc.create(MEDIUM, seed=3, workflow="discussion")
    async for _ in svc.events(sid):
        pass
    assert env.fake.calls
    sent = json.dumps([[x.content for x in c.messages] for c in env.fake.calls], ensure_ascii=False)
    assert mark not in sent


def test_only_state_plugin_writes_state():
    writer = re.compile(r"\.(save_member_state|clear_member_state)\(")
    allowed = {SRC / "core" / "member" / "state.py"}
    for f in SRC.rglob("*.py"):
        if writer.search(f.read_text(encoding="utf-8")):
            assert f in allowed, f
    keys = re.compile(r"[\"']member_state:")
    for f in SRC.rglob("*.py"):
        if keys.search(f.read_text(encoding="utf-8")):
            assert f.name == "repository.py", f


def test_engine_wires_state_plugin():
    env = Env()
    engine = MemberEngine(env.rt, lambda: ["V1"])
    assert isinstance(engine.state, StatePlugin)
    assert engine.state.mood_of("V1", NOON) == 0


def test_hours_follow_home_zone():
    from roundtable.core.hometz import home_zone

    env = Env()
    st = StatePlugin(env.rt)  # 不传 tz：跟终端时钟选的主时区
    home_zone(env.rt).set("Asia/Shanghai")
    assert st.hour(NOON) == 20  # 北京时间晚上八点
    home_zone(env.rt).set("Europe/London")
    assert st.hour(NOON) == 12


def test_owl_without_clock_stays_up_late():
    rules = Env().rt.config.roundtable.chat.engine.state.baseline
    b = derive_baseline(profile(daily="上午不起太早。深夜才睡。"), "x", rules)
    assert b.night_owl and b.sleep >= 25
