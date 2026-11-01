"""P7 生活引擎 L0：事件库挑事可复现、大部分日子平淡、长线剧情的前因后果、频率上限、按爱好挑去处、
住户变化、只发事件（心情由 P3 接、记忆由 P2 接）、场景里「最近各自的事」。"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from roundtable.core.chat import ChatHub
from roundtable.core.config.schema import StoryStep
from roundtable.core.life import LifeEngine
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.engine import MemberEngine
from roundtable.core.member.state import StatePlugin
from roundtable.core.member.story import STORY, StoryEngine, story_meta
from roundtable.core.member.story.engine import REST, parse_mark
from roundtable.core.memory import MemoryKeeper, life_line
from roundtable.core.storage.repository import iso

from ...orchestrator.conftest import Env
from .common import NAMES, T0, likes, run_daily, run_days, spec, story_env

SICK = spec(
    id="flu",
    group="sick",
    chance=1.0,
    steps=[
        StoryStep(text="嗓子疼", tags=["story.sick"]),
        StoryStep(day=1, text="躺着", tags=["story.sick"], rest=True),
        StoryStep(day=[2, 4], text="好了", tags=["story.better"]),
    ],
)


def test_library_in_repo_config_is_valid_and_brand_free():
    env = Env()
    rules = env.config.roundtable.chat.life.story
    assert rules.enabled and len(rules.stories) >= 20
    assert {s.trigger for s in rules.stories} >= {"daily", "arrive", "welcome", "farewell"}
    names = {m.id.lower() for m in env.config.models.models} | {
        m.vendor.lower() for m in env.config.models.models
    }
    text = str(rules.model_dump()).lower()
    assert not [n for n in names if n and n in text]


def test_picking_is_reproducible_and_most_days_are_plain():
    a, b = Env(), Env()
    first, second = run_daily(a, 60), run_daily(b, 60)
    assert [(h.at, h.story, h.members, h.text) for h in first] == [
        (h.at, h.story, h.members, h.text) for h in second
    ]
    per_day = Counter((h.members[0], h.at.date()) for h in first)
    assert max(per_day.values()) <= Env().config.roundtable.chat.life.story.max_per_day + 1
    # 大部分日子是平淡的：60 天 × 6 人里，大多数「人·天」什么都没发生
    assert len(per_day) < 60 * 6 * 0.5


def test_a_story_unfolds_over_days_and_rest_day_keeps_her_in():
    env = story_env(stories=[SICK], group_cooldown_days={"sick": 90})
    got = run_daily(env, 6, {"V1": "阿一"})
    texts = [h.text for h in got]
    assert texts == ["嗓子疼", "躺着", "好了"]
    assert got[1].at.date() == got[0].at.date() + timedelta(days=1)
    assert 2 <= (got[2].at.date() - got[0].at.date()).days <= 4
    eng = StoryEngine(env.rt)
    rest_day = got[1].at.replace(hour=20, minute=0)
    assert eng.prefer("V1", rest_day, "gym", "exercise") == ("dorm", REST)
    # 上班时间照常（生病只影响空闲时段）
    assert eng.prefer("V1", rest_day.replace(hour=10), "office", "idle") == ("office", "idle")


def test_group_cooldown_caps_how_often_she_gets_sick():
    env = story_env(stories=[SICK], group_cooldown_days={"sick": 90})
    got = run_daily(env, 120, {"V1": "阿一"})
    starts = [h for h in got if h.step == 0]
    assert len(starts) == 2 and (starts[1].at - starts[0].at).days >= 90


def test_no_sick_story_while_the_life_phase_has_her_down_with_a_cold():
    import json

    from roundtable.core.member.phase import PHASE_KEY

    env = story_env(stories=[SICK], phase_groups={"sick": ["小感冒"]})
    row = {"kind": "小感冒", "start": iso(T0), "end": iso(T0 + timedelta(days=3))}
    env.rt.repo.set_setting(PHASE_KEY, json.dumps({"V1": row}))
    got = run_daily(env, 6, {"V1": "阿一"})
    assert got and got[0].at.date() >= (T0 + timedelta(days=3)).date()


def test_need_likes_only_happens_to_those_whose_card_mentions_it():
    sprain = spec(
        id="sprain", chance=1.0, likes=["跑步"], need_likes=True, steps=[{"text": "扭了"}]
    )
    env = story_env(stories=[sprain])
    likes(env, "V2", 最爱=["跑步"])
    got = run_daily(env, 3, {"V1": "阿一", "V2": "阿二"})
    assert {h.members[0] for h in got} == {"V2"}


def test_free_time_follows_her_own_likes_not_a_rotation():
    env = Env()
    likes(env, "V1", 最爱=["星星", "天文", "夜空"])
    likes(env, "V2", 最爱=["游戏", "通关", "抽卡"])
    eng = StoryEngine(env.rt)
    picks = {"V1": Counter(), "V2": Counter()}
    for d in range(40):
        slot = T0 + timedelta(days=d, hours=20)
        for m in picks:
            picks[m][eng.prefer(m, slot, "gym", "exercise")] += 1
    assert picks["V1"].most_common(1)[0][0] == ("rooftop", "stargaze")
    assert picks["V2"].most_common(1)[0][0] == ("lounge", "game")
    # 不是只会去一个地方（还有别的去处）
    assert len(picks["V1"]) >= 3
    # 作息里不是空闲的（睡觉、吃饭、上班）不改
    assert eng.prefer("V1", T0 + timedelta(hours=12), "canteen", "eat") == ("canteen", "eat")


def test_with_other_story_names_a_companion_and_lists_both():
    duo = spec(id="duo", chance=1.0, with_other=True, steps=[{"text": "{name}陪{other}聊天"}])
    env = story_env(stories=[duo], max_per_day=1)
    got = run_daily(env, 1, {"V1": "阿一", "V2": "阿二"})
    h = next(x for x in got if x.members[0] == "V1")
    assert h.members == ["V1", "V2"] and h.text == "阿一陪阿二聊天"


def test_roster_changes_become_welcome_and_farewell_events():
    env = Env()
    eng = StoryEngine(env.rt)
    t = T0 + timedelta(hours=10)
    first = {"V1": "阿一", "V2": "阿二", "V3": "阿三"}
    assert not [
        h
        for h in eng.advance(t, t + timedelta(minutes=30), first)
        if h.story in ("arrive", "welcome", "farewell")
    ]  # 第一次只记名单
    now = {"V1": "阿一", "V2": "阿二", "V4": "阿四"}
    got = eng.advance(t + timedelta(minutes=30), t + timedelta(hours=1), now)
    by = {h.story: h for h in got if h.story in ("arrive", "welcome", "farewell")}
    assert by["arrive"].members == ["V4"]
    assert by["welcome"].members == ["V1", "V2"] and "阿四" in by["welcome"].text
    assert by["farewell"].members == ["V1", "V2"] and "阿三" in by["farewell"].text
    assert all("煽情" not in h.text for h in by.values())


def test_story_rows_feed_mood_via_p3_and_memory_via_p2():
    env = story_env(stories=[SICK], group_cooldown_days={"sick": 90})
    hub = ChatHub(env.rt)
    life = LifeEngine(env.rt, hub, MemoryKeeper(env.rt, hub.everyone))
    first = T0 + timedelta(hours=8)
    env.rt.budget.now = lambda: first
    happenings = life.story.advance(first, first + timedelta(hours=16), NAMES)
    for h in happenings:
        env.rt.repo.add_life_event(iso(h.at), STORY, h.members, place=h.place, topic=h.topic)
    row = env.rt.repo.life_events_between(iso(first), iso(first + timedelta(days=1)))[0]
    assert row["kind"] == STORY and parse_mark(row["topic"])[0] == "flu"
    assert "嗓子疼" in life_line(row)  # 记忆材料里是一句自然话
    meta = story_meta(env.rt, row["topic"])
    assert meta["tags"] == ["story.sick"]
    # 成员引擎转到总线时带上标签，P3 按反射表接（只有一套反射表，8 位相同）
    bus = EventBus(env.rt)
    state = StatePlugin(env.rt, bus, lambda: NAMES)
    at = datetime_of(row)
    before = state.read("V1", at).energy
    bus.publish(Event("life.event", at, "V1", {"kind": STORY, **meta}))
    assert state.read("V1", at).energy < before


def datetime_of(row):
    from datetime import datetime

    return datetime.fromisoformat(row["at"])


def test_engine_bridge_attaches_story_tags():
    env = Env()
    got = []
    eng = MemberEngine(env.rt, lambda: ["V1"])
    eng.bus.subscribe("life.event", got.append)
    at = T0 + timedelta(hours=10)
    topic = "地点：自己的宿舍（扭了脚）\n生活事件：sprain/0"
    env.rt.repo.add_life_event(iso(at), STORY, ["V1"], place="dorm", topic=topic)
    eng.bridge(at - timedelta(minutes=1), at + timedelta(minutes=1))
    assert got and got[0].data["story"] == "sprain" and got[0].data["tags"] == ["story.sick"]


async def test_life_tick_writes_story_rows_and_scenes_mention_recent_life():
    cook = spec(id="cook", chance=1.0, steps=[{"text": "学会了一道新菜", "tags": ["story.good"]}])
    env = story_env(stories=[cook])
    hub = ChatHub(env.rt)
    life = LifeEngine(env.rt, hub, MemoryKeeper(env.rt, hub.everyone))
    env.rt.budget.now = lambda: T0
    start = T0 + timedelta(hours=8)
    env.rt.repo.set_setting("life.last_tick", start.isoformat())
    out = await life.tick(start + timedelta(hours=14))
    assert out["stories"] >= 1
    rows = [
        r
        for r in env.rt.repo.life_events_between(iso(start), iso(start + timedelta(days=1)))
        if r["kind"] == STORY
    ]
    assert rows and all("学会了一道新菜" in r["topic"] for r in rows)
    plans = life.plan(start + timedelta(hours=10), start + timedelta(hours=14))
    social = [p for p in plans if p.kind != "solo"]
    assert social and any("最近各自的事" in p.topic for p in social)
    line = next(p.topic for p in social if "最近各自的事" in p.topic).splitlines()[-1]
    assert "「" not in line and '"' not in line  # 背景，不给可照抄的原句
    assert "生活事件：" not in line


def test_half_hour_ticks_match_daily_ticks_in_what_happens():
    env_a = story_env(stories=[SICK], group_cooldown_days={"sick": 90})
    env_b = story_env(stories=[SICK], group_cooldown_days={"sick": 90})
    fine = run_days(env_a, 4, {"V1": "阿一"})
    coarse = run_daily(env_b, 4, {"V1": "阿一"})
    assert [h.text for h in fine] == [h.text for h in coarse]


def test_state_view_has_no_scores():
    env = story_env(stories=[SICK], group_cooldown_days={"sick": 90})
    run_daily(env, 2, {"V1": "阿一"})
    view = StoryEngine(env.rt).state("V1", {"V1": "阿一"})
    assert view["recent"] and view["upcoming"]
    assert "score" not in str(view) and "分" not in str(view["recent"])
