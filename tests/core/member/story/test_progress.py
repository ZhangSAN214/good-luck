"""L1 爱好进度：在做的东西优先从她「最爱」里挑；前一天生活里做了相关的事才往前推；没有相关活动的
（追剧）按每天的机会推；推满就「看完了」（好事、算擅长练习），隔几天再开始下一样；可复现。"""

from __future__ import annotations

from datetime import date, timedelta

from roundtable.core.member.story import STORY, StoryEngine, Talents, store
from roundtable.core.member.story.progress import DONE, MID, START, Progress
from roundtable.core.storage.repository import iso

from .common import T0, likes, run_daily, story_env

READ = "地点：自己的宿舍（看书）"


def quiet_env(**kinds):
    """事件库清空，只看爱好进度（kinds 可整个换掉）。"""
    base = story_env().config.roundtable.chat.life.story
    progress = base.progress
    if kinds:
        progress = progress.model_copy(update={"kinds": kinds})
    return story_env(stories=[], progress=progress)


def book(**kw):
    from roundtable.core.config.schema import ProgressKind

    args = {
        "words": ["小说"],
        "things": ["一本小说"],
        "acts": ["dorm:read"],
        "total": [6, 6],
        "per": [2, 2],
        "gap_days": 3,
        "mid_chance": 1.0,
        "domain": "长文阅读",
    }
    return ProgressKind(**(args | kw))


def read_days(env, member, days):
    for d in days:
        at = T0 + timedelta(days=d, hours=21)
        env.rt.repo.add_life_event(iso(at), "solo", [member], place="dorm-1", topic=READ)


def test_repo_config_has_book_show_game():
    env = story_env()
    kinds = env.config.roundtable.chat.life.story.progress.kinds
    assert {"book", "show", "game"} <= set(kinds)
    assert all(
        k.domain in env.config.roundtable.chat.life.story.talent.domains or not k.domain
        for k in kinds.values()
    )


def test_thing_comes_from_her_favorites_first():
    env = quiet_env(book=book())
    likes(env, "V1", 最爱=["★推理小说", "猫"])
    got = run_daily(env, 1, {"V1": "阿一", "V2": "阿二"})
    by = {h.members[0]: h.text for h in got if h.story == "progress_book"}
    assert by["V1"] == "开始看推理小说"  # 去掉了星号
    assert by["V2"] == "开始看一本小说"  # 没有提到的从配置里挑


def test_reading_yesterday_moves_it_forward_until_done():
    env = quiet_env(book=book())
    read_days(env, "V1", range(0, 3))
    got = [h for h in run_daily(env, 8, {"V1": "阿一"}) if h.story == "progress_book"]
    assert [h.step for h in got][:4] == [START, MID, MID, DONE]
    assert got[1].text == "一本小说看到第2章了" and got[3].text == "把一本小说看完了"
    assert got[3].tags == ["story.good"] and got[3].domain == "长文阅读"
    assert got[0].tags == []


def test_no_reading_no_progress_and_gap_before_the_next_one():
    env = quiet_env(book=book())
    got = [h for h in run_daily(env, 6, {"V1": "阿一"}) if h.story == "progress_book"]
    assert [h.step for h in got] == [START]  # 没看书就不动
    st = store.load(env.rt, "V1")
    assert st["progress"]["book"]["n"] == 0
    env2 = quiet_env(book=book())
    read_days(env2, "V1", range(0, 3))
    got = [h for h in run_daily(env2, 12, {"V1": "阿一"}) if h.story == "progress_book"]
    done = next(h for h in got if h.step == DONE)
    nxt = [h for h in got if h.step == START and h.at > done.at]
    assert nxt and (nxt[0].at.date() - done.at.date()).days >= 3


def test_daily_kind_moves_without_activities():
    env = quiet_env(show=book(acts=[], daily=1.0, unit="集", total=[4, 4], per=[1, 1]))
    got = [h for h in run_daily(env, 6, {"V1": "阿一"}) if h.story == "progress_show"]
    assert [h.step for h in got] == [START, MID, MID, MID, DONE]


def test_finishing_counts_as_practice_but_starting_does_not():
    env = quiet_env(book=book())
    read_days(env, "V1", range(0, 3))
    got = [h for h in run_daily(env, 6, {"V1": "阿一"}) if h.story == "progress_book"]
    for h in got:  # 生活引擎写成生活事件（和 life.tick 一样）
        env.rt.repo.add_life_event(iso(h.at), STORY, h.members, place=h.place, topic=h.topic)
    p = Talents(env.rt).practice(["V1"], (T0 + timedelta(days=9)).date(), T0.tzinfo)
    n = sum(p["V1"]["长文阅读"].values())
    # 看书 3 回 + 中途 2 次 + 看完 1 次；「开始看」不算练习
    assert n == 3 + 2 + 1


def test_daily_cap_still_applies_but_progress_keeps_moving():
    env = quiet_env(a=book(), b=book(things=["一本诗集"]), c=book(things=["一本散文"]))
    got = run_daily(env, 1, {"V1": "阿一"})
    assert len(got) == env.config.roundtable.chat.life.story.max_per_day
    assert set(store.load(env.rt, "V1")["progress"]) == {"a", "b", "c"}


def test_reproducible_and_view_is_plain_words():
    a, b = quiet_env(book=book()), quiet_env(book=book())
    for env in (a, b):
        read_days(env, "V1", range(0, 2))
    ra, rb = run_daily(a, 5, {"V1": "阿一"}), run_daily(b, 5, {"V1": "阿一"})
    assert [(h.at, h.text) for h in ra] == [(h.at, h.text) for h in rb]
    view = StoryEngine(a.rt).state("V1", {"V1": "阿一"})["progress"]
    assert view and view[0]["text"].startswith("一本小说")
    assert "score" not in str(view)


def test_yesterday_reads_only_gather_and_solo_rows():
    env = quiet_env(book=book())
    read_days(env, "V1", [0])
    at = T0 + timedelta(hours=22)
    env.rt.repo.add_life_event(iso(at), "story", ["V1"], place="dorm", topic=READ)
    did = Progress(env.rt).yesterday(["V1"], date(2026, 11, 3), T0.tzinfo)
    assert did["V1"]["dorm:read"] == 1
