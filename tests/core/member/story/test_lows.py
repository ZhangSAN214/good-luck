"""L4 健康与低谷：病了（事件库的或生活阶段的小感冒）常一起玩的朋友照顾一次；接连不顺 → 低落（说得出
原因）→ 有人陪 → 缓过来；累久了在最近的周末歇一天；生活阶段只读、格式不动；只看生活不看办公室。"""

from __future__ import annotations

import json
from datetime import timedelta

from roundtable.core.config.schema import StoryStep
from roundtable.core.member.phase import PHASE_KEY
from roundtable.core.member.story import StoryEngine, story_meta
from roundtable.core.member.story.engine import REST
from roundtable.core.storage.repository import iso

from ...orchestrator.conftest import Env
from .common import T0, run_daily, spec, story_env

NAMES = {"V1": "阿一", "V2": "阿二", "V3": "阿三"}
BASE = Env().config.roundtable.chat.life.story.lows


def env_with(stories=(), **lows):
    return story_env(stories=list(stories), lows=BASE.model_copy(update=lows))


SPRAIN = spec(
    id="sprain",
    chance=1.0,
    cooldown_days=60,
    steps=[
        StoryStep(text="跑步的时候崴了一下脚", tags=["story.sick"], rest=True),
        StoryStep(day=2, text="脚好了", tags=["story.better"]),
    ],
)


def only(env, member, story_):
    return lambda h: h.members[0] == member and h.story == story_


def test_friend_she_hangs_out_with_brings_care_once_per_illness():
    env = env_with([SPRAIN], care_chance=1.0, low_chance=0.0, rest_chance=0.0)
    # 最近两周阿一常和阿三一起
    for d in range(1, 10):
        at = T0 - timedelta(days=d, hours=-20)
        env.rt.repo.add_life_event(iso(at), "gather", ["V1", "V3"], place="lounge", topic="地点")
    got = run_daily(env, 6, NAMES)
    care = [h for h in got if h.story == "low_care" and h.members[0] == "V1"]
    assert len(care) == 1  # 一场病一次
    assert care[0].members == ["V1", "V3"] and "阿三" in care[0].text and "阿一" in care[0].text
    assert care[0].tags == ["story.cared"]


def test_phase_cold_gets_care_and_keeps_her_in_without_touching_phase_data():
    env = env_with(care_chance=1.0, low_chance=0.0, rest_chance=0.0)
    row = {"kind": "小感冒", "start": iso(T0), "end": iso(T0 + timedelta(days=2))}
    raw = json.dumps({"V2": row})
    env.rt.repo.set_setting(PHASE_KEY, raw)
    got = run_daily(env, 3, NAMES)
    assert [h.members[0] for h in got if h.story == "low_care"] == ["V2"]
    eng = StoryEngine(env.rt)
    evening = T0 + timedelta(days=1, hours=20)
    assert eng.prefer("V2", evening, "gym", "exercise") == ("dorm", REST)
    assert eng.prefer("V2", evening.replace(hour=10), "office", "idle") == ("office", "idle")
    assert env.rt.repo.setting(PHASE_KEY) == raw  # 只读：成长总账读的格式不动


def test_a_run_of_bad_luck_leads_to_a_low_with_a_cause_comfort_and_recovery():
    bad1 = spec(
        id="lost",
        chance=1.0,
        cooldown_days=90,
        steps=[{"text": "钥匙丢了，在门口等了半天", "tags": ["story.bad"]}],
    )
    bad2 = spec(
        id="tooth",
        chance=1.0,
        cooldown_days=90,
        steps=[{"day": 1, "text": "牙疼得睡不着", "tags": ["story.bad"]}],
    )
    env = env_with([bad1, bad2], care_chance=0.0, low_chance=1.0, rest_chance=0.0)
    got = run_daily(env, 12, NAMES)
    arc = [h for h in got if h.story == "low_mood" and h.members[0] == "V1"]
    assert [h.step for h in arc] == [0, 1, 2]
    assert "钥匙丢了" in arc[0].text and "牙疼" in arc[0].text  # 说得出原因
    assert arc[0].tags == ["story.low"] and arc[1].tags == ["story.comforted"]
    assert len(arc[1].members) == 2 and arc[2].tags == ["story.better"]
    assert 3 <= (arc[2].at.date() - arc[0].at.date()).days <= 5
    # 冷却：不会一直低落
    assert (
        len([h for h in got if h.story == "low_mood" and h.step == 0 and h.members[0] == "V1"]) == 1
    )


def test_tired_for_days_means_a_rest_day_on_the_weekend():
    late = spec(
        id="late", chance=1.0, cooldown_days=0, steps=[{"text": "熬到两点", "tags": ["story.late"]}]
    )
    env = env_with([late], care_chance=0.0, low_chance=0.0, rest_chance=1.0, need_tired=3)
    got = run_daily(env, 7, {"V1": "阿一"})
    rest = [h for h in got if h.story == "low_rest"]
    assert rest and rest[0].at.weekday() >= 5 and rest[0].tags == ["story.rest"]
    eng = StoryEngine(env.rt)
    assert eng.prefer("V1", rest[0].at.replace(hour=20), "gym", "exercise") == ("dorm", REST)


def test_busy_phase_counts_as_tiredness():
    env = env_with(care_chance=0.0, low_chance=0.0, rest_chance=1.0, need_tired=3)
    row = {"kind": "忙季", "start": iso(T0), "end": iso(T0 + timedelta(days=6))}
    env.rt.repo.set_setting(PHASE_KEY, json.dumps({"V1": row}))
    got = run_daily(env, 7, {"V1": "阿一"})
    assert [h for h in got if h.story == "low_rest"]


def test_office_work_never_counts():
    """办公室的活不算累也不算不顺（生活只读工作记录，但不拿来当证据）。"""
    env = env_with(care_chance=1.0, low_chance=1.0, rest_chance=1.0, need_tired=1, need_bad=1)
    for d in range(7):
        at = T0 + timedelta(days=d, hours=10)
        topic = "地点：公寓楼办公室（上班）\n今天的事：阿一 今天办了「写报告」"
        env.rt.repo.add_life_event(iso(at), "solo", ["V1"], place="office", topic=topic)
    assert not [h for h in run_daily(env, 8, NAMES) if h.story.startswith("low_")]


def test_story_meta_tags():
    env = Env()
    assert story_meta(env.rt, "地点：x（y）\n生活事件：low_mood/1")["tags"] == ["story.comforted"]
