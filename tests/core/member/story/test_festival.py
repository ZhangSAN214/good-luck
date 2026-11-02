"""L2 节日纪念日：读日历（不另算日子）；节日提前有人准备、当天大家一起过；家乡的节日她领头、会想家；
成员生日前一天有人偷偷准备礼物（礼物从寿星的最爱里挑）、当天大家给她过，两天对得上；入住周年；
一天只排一次（半小时推进和一天推进一样）；标签交给 P3。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.member.story import StoryEngine, story_meta
from roundtable.core.member.story.festival import KEY
from roundtable.core.storage.repository import iso

from ...orchestrator.conftest import Env
from .common import likes, story_env

HALLOWEEN = datetime(2026, 10, 30, tzinfo=UTC)  # 万圣夜前一天
NAMES = {"V1": "阿一", "V2": "阿二", "V3": "阿三"}


def quiet():
    """事件库和爱好进度都关掉，只看节日。"""
    return story_env(stories=[], festival=Env().config.roundtable.chat.life.story.festival)


def run(env, start, days, names=NAMES, step=timedelta(days=1)):
    eng = StoryEngine(env.rt)
    out, t = [], start
    while t < start + timedelta(days=days):
        out += eng.advance(t, t + step - timedelta(seconds=1), names)
        t += step
    return [h for h in out if h.story.startswith("fest_")]


def with_birthday(env, member, md):
    personas = env.rt.config.personas.model_copy(update={"birthdays": {member: md}})
    env.rt.config = env.rt.config.model_copy(update={"personas": personas})


def test_festival_prep_then_everyone_celebrates():
    got = run(quiet(), HALLOWEEN, 2)
    prep, day = got
    assert prep.story == "fest_prep" and len(prep.members) == 1 and "南瓜" in prep.text
    assert prep.at.date() == HALLOWEEN.date()
    assert day.story == "fest_day" and sorted(day.members) == sorted(NAMES)
    assert day.at.date() == (HALLOWEEN + timedelta(days=1)).date()
    assert day.tags == ["story.good"] and prep.tags == []


def test_home_festival_led_by_her_and_she_misses_home():
    env = quiet()
    likes(env, "V2", 最爱=["老家的月饼"], 来历=["中国杭州"])
    got = run(env, datetime(2026, 9, 24, tzinfo=UTC), 2)
    by = {h.story: h for h in got}
    assert by["fest_prep"].members == ["V2"] and by["fest_day"].members[0] == "V2"
    assert by["fest_homesick"].members == ["V2"] and "想家" in by["fest_homesick"].text
    assert by["fest_homesick"].tags == ["story.homesick"]


def test_birthday_gift_prepared_the_day_before_matches_the_day():
    env = quiet()
    with_birthday(env, "V1", "11-10")
    likes(env, "V1", 最爱=["★星星"])
    got = run(env, datetime(2026, 11, 9, tzinfo=UTC), 2)
    prep = next(h for h in got if h.story == "fest_bday_prep")
    day = next(h for h in got if h.story == "fest_bday")
    giver = NAMES[prep.members[0]]
    assert prep.members[0] != "V1" and "阿一" in prep.text
    assert day.members[0] == "V1" and sorted(day.members) == sorted(NAMES)
    assert giver in day.text and "星星" in day.text  # 送礼的人和礼物前后对得上


def test_joined_anniversary():
    env = quiet()
    first = datetime(2025, 11, 12, 10, tzinfo=UTC)
    env.rt.repo.add_chat_message("life", "V3", "大家好", created_at=iso(first))
    got = run(env, datetime(2026, 11, 12, tzinfo=UTC), 1)
    joined = [h for h in got if h.story == "fest_joined"]
    assert joined and joined[0].members == ["V3"] and "1年" in joined[0].text


def test_planned_once_a_day_and_half_hour_ticks_match():
    a, b = quiet(), quiet()
    daily = run(a, HALLOWEEN, 2)
    fine = run(b, HALLOWEEN, 2, step=timedelta(minutes=30))
    assert [(h.story, h.text) for h in daily] == [(h.story, h.text) for h in fine]
    assert a.rt.repo.setting(KEY) == (HALLOWEEN + timedelta(days=1)).date().isoformat()


def test_off_when_calendar_or_festival_disabled():
    base = Env().config.roundtable.chat.life.story
    env = story_env(stories=[], festival=base.festival.model_copy(update={"enabled": False}))
    assert not run(env, HALLOWEEN, 2)


def test_story_meta_tags_for_festival_rows():
    env = Env()
    assert story_meta(env.rt, "地点：休息室（过节）\n生活事件：fest_day/0")["tags"] == [
        "story.good"
    ]
