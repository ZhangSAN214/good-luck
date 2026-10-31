"""L10 觉醒擅长：证据只有人物卡种子 + 生活练习；萌芽 → 觉醒（说得出依据）→ 深耕（有人来请教）→
卡住 / 想通；长期不练会生疏；P6 写档案、能撤回；8 位同一套规则；和办公室表现无关。"""

from __future__ import annotations

from datetime import timedelta

from roundtable.core.chat import ChatHub
from roundtable.core.growth import GrowthKeeper
from roundtable.core.member.growth import GrowthLog
from roundtable.core.member.profile import ProfileStore
from roundtable.core.member.story import StoryEngine, Talents, store
from roundtable.core.storage.repository import iso

from .common import NAMES, T0, likes, run_daily, story_env

STARS = "地点：公寓楼天台（看星星）"


def gaze(env, member: str, days: range, *, per_day: int = 1) -> None:
    """她这些天在天台看星星（生活里的练习）。"""
    for d in days:
        for k in range(per_day):
            at = T0 + timedelta(days=d, hours=20, minutes=k)
            env.rt.repo.add_life_event(iso(at), "solo", [member], place="rooftop", topic=STARS)


def quiet_env(**talent):
    """事件库清空（只看擅长），擅长规则可改几项。"""
    base = story_env().config.roundtable.chat.life.story.talent
    return story_env(stories=[], talent=base.model_copy(update=talent))


def test_seeds_alone_never_awaken_a_talent():
    env = quiet_env()
    likes(env, "V1", 最爱=["星星", "天文", "星空", "宇宙"])
    run_daily(env, 40, {"V1": "阿一"})
    st = store.load(env.rt, "V1")
    assert not Talents(env.rt).awakened(st)


def test_practice_plus_seed_awakens_with_evidence_and_p6_writes_profile():
    env = quiet_env()
    likes(env, "V1", 最爱=["星星", "天文"])
    gaze(env, "V1", range(0, 20))
    got = run_daily(env, 21, {"V1": "阿一"})
    awake = [h for h in got if h.story == "talent_awake"]
    assert len(awake) == 1 and awake[0].domain == "天文科学"
    assert "看星星" in awake[0].text and "回" in awake[0].text  # 说得出依据，不凭空吹
    # P6：以成长的身份写进 likes.擅长，记进时间线，能撤回
    hub = ChatHub(env.rt)
    keeper = GrowthKeeper(env.rt, hub.everyone)
    assert keeper.talents(T0 + timedelta(days=21)) == 1
    prof = ProfileStore(env.rt)
    assert prof.current("V1").get("likes.擅长") == ["天文科学"]
    assert prof.history("V1")[0]["author"] == "growth"
    log = GrowthLog(env.rt)
    entry = log.entries("V1")[0]
    assert entry["path"] == "likes.擅长" and entry["value"] == "天文科学" and entry["evidence"]
    log.revert("V1", entry["id"])
    assert not prof.current("V1").get("likes.擅长")
    # 取走就清掉：不会重复写
    assert keeper.talents(T0 + timedelta(days=22)) == 0


def test_same_evidence_same_outcome_for_every_member():
    env = quiet_env()
    for m in ("V1", "V2"):
        likes(env, m, 最爱=["星星"])
        gaze(env, m, range(0, 20))
    got = run_daily(env, 21, {"V1": "阿一", "V2": "阿二"})
    awake = sorted((h.members[0], h.domain) for h in got if h.story == "talent_awake")
    assert awake == [("V1", "天文科学"), ("V2", "天文科学")]


def test_deepening_brings_people_to_ask_her_and_a_natural_known_line():
    env = quiet_env(deep_days=5, deep_practice=5, ask_chance=1.0, stuck_chance=0.0)
    likes(env, "V1", 最爱=["星星"])
    gaze(env, "V1", range(0, 40))
    got = run_daily(env, 30, {"V1": "阿一", "V2": "阿二"})
    asks = [h for h in got if h.story == "talent_ask"]
    assert asks and asks[0].members[0] == "V1" and len(asks[0].members) == 2
    assert "阿二" in asks[0].text and "天文科学" in asks[0].text
    st = store.load(env.rt, "V1")
    known = Talents(env.rt).known(st, "阿一")
    assert known and "阿一" in known[0]
    assert not any(ch.isdigit() for ch in known[0])  # 自然话，没有等级和分数


def test_stuck_then_unstuck():
    env = quiet_env(deep_days=5, deep_practice=5, ask_chance=0.0, stuck_chance=1.0)
    likes(env, "V1", 最爱=["星星"])
    gaze(env, "V1", range(0, 40))
    got = run_daily(env, 30, {"V1": "阿一"})
    stuck = [h for h in got if h.story == "talent_stuck" and h.step == 0]
    unstuck = [h for h in got if h.story == "talent_stuck" and h.step == 1]
    assert stuck and unstuck and unstuck[0].at > stuck[0].at
    assert stuck[0].tags == ["story.stuck"] and unstuck[0].tags == ["story.unstuck"]
    assert "想通" in unstuck[0].text
    # 卡着的时候不会又卡一次：每次卡住后面都跟着一次想通
    assert len(stuck) == len(unstuck) or len(stuck) == len(unstuck) + 1


def test_long_neglect_makes_her_rusty_and_p6_takes_it_off():
    env = quiet_env()
    likes(env, "V1", 最爱=["星星"])
    gaze(env, "V1", range(0, 20))
    run_daily(env, 21, {"V1": "阿一"})
    hub = ChatHub(env.rt)
    keeper = GrowthKeeper(env.rt, hub.everyone)
    keeper.talents(T0 + timedelta(days=21))
    eng = StoryEngine(env.rt)
    rusty = []
    for i in range(21, 80):  # 不再去天台
        a = T0 + timedelta(days=i)
        rusty += [
            h
            for h in eng.advance(a, a + timedelta(days=1) - timedelta(seconds=1), {"V1": "阿一"})
            if h.story == "talent_rusty"
        ]
    assert len(rusty) == 1
    keeper.talents(T0 + timedelta(days=80))
    assert not ProfileStore(env.rt).current("V1").get("likes.擅长")


def test_max_talents_is_respected():
    env = quiet_env(max_talents=1)
    likes(env, "V1", 最爱=["星星", "游戏"])
    gaze(env, "V1", range(0, 20))
    game = "地点：公寓楼休息室（打游戏）"
    for d in range(0, 20):
        at = T0 + timedelta(days=d, hours=21)
        env.rt.repo.add_life_event(iso(at), "solo", ["V1"], place="lounge", topic=game)
    got = run_daily(env, 25, {"V1": "阿一"})
    assert len([h for h in got if h.story == "talent_awake"]) == 1


def test_awakened_talent_tilts_free_time_toward_it():
    env = quiet_env()
    likes(env, "V1", 最爱=["星星"])
    gaze(env, "V1", range(0, 20))
    run_daily(env, 21, {"V1": "阿一"})
    eng = StoryEngine(env.rt)
    st = store.load(env.rt, "V1")
    assert Talents(env.rt).awakened(st)
    picks = [
        eng.prefer("V1", T0 + timedelta(days=30 + d, hours=20), "gym", "exercise")
        for d in range(40)
    ]
    assert picks.count(("rooftop", "stargaze")) > len(picks) / 3


def test_office_work_is_never_talent_evidence():
    """办公室的模型是电脑：办过多少活都不算练习（练习只从生活事件里来）。"""
    env = quiet_env()
    likes(env, "V1", 最爱=["代码", "编程"])
    # 生活事件里只有「办公室」的事（工作被生活只读后也只是背景，不算任何领域的练习）
    for d in range(20):
        at = T0 + timedelta(days=d, hours=10)
        topic = "地点：公寓楼办公室（上班）\n今天的事：阿一 今天办了「写一个排序算法」"
        env.rt.repo.add_life_event(iso(at), "solo", ["V1"], place="office", topic=topic)
    run_daily(env, 25, {"V1": "阿一"})
    assert not Talents(env.rt).awakened(store.load(env.rt, "V1"))
    assert NAMES  # 名单照常
