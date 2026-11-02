"""L5 旅行：计划 → 收拾 → 出发 → 途中 → 回来带东西 → 早睡；在外面那几天不在家、工作照常。"""

from __future__ import annotations

from datetime import timedelta

from roundtable.core.member.story import StoryEngine, store, story_meta
from roundtable.core.member.story.travel import AWAY
from roundtable.core.office.apartment import building, life_spots

from ...orchestrator.conftest import Env
from .common import NAMES, T0, likes, run_daily, story_env

ORDER = ["travel_plan", "travel_pack", "travel_go", "travel_trip", "travel_back", "travel_rest"]


def travel_env(**kw):
    base = Env().config.roundtable.chat.life.story
    rules = base.travel.model_copy(update={"chance": 1.0, **kw})
    return story_env(stories=[], travel=rules)


def trips_of(got, member):
    return [h for h in got if h.story.startswith("travel_") and h.members[0] == member]


def test_a_trip_unfolds_in_order_and_brings_something_back():
    env = travel_env()
    got = run_daily(env, 20)
    legs = trips_of(got, "V1")
    assert legs, "chance 1 时一定会出门"
    first = [h for h in legs if h.at <= legs[0].at + timedelta(days=12)]
    kinds = [h.story for h in first]
    # 一趟里的几步按顺序发生（同一种可以有几件：途中每天一件）
    seen = [k for i, k in enumerate(kinds) if i == 0 or kinds[i - 1] != k]
    assert [k for k in ORDER if k in seen] == seen[: len([k for k in ORDER if k in seen])]
    assert seen[0] == "travel_plan" and "travel_go" in seen and "travel_back" in seen
    assert all(a.at <= b.at for a, b in zip(first, first[1:], strict=False))
    dest = store.load(env.rt, "V1")["trips"][0]["dest"]
    assert all(dest in h.text for h in first if h.story != "travel_rest")
    back = next(h for h in first if h.story == "travel_back")
    if len(back.members) == 2:  # 回来给常一起玩的朋友带了东西（两人都记一笔）
        assert NAMES[back.members[1]] in back.text
    go = next(h for h in first if h.story == "travel_go")
    assert go.place == AWAY and story_meta(env.rt, go.topic)["tags"] == ["story.travel"]
    assert "外地" in go.topic


def test_away_days_have_no_other_little_things_and_work_goes_on():
    env = story_env(
        travel=Env().config.roundtable.chat.life.story.travel.model_copy(update={"chance": 1.0})
    )
    got = run_daily(env, 30)
    st = store.load(env.rt, "V1")
    trip = st["trips"][0]
    eng = StoryEngine(env.rt)
    out_days = {
        h.at.date()
        for h in got
        if h.members[0] == "V1" and trip["go"] <= h.at.date().isoformat() <= trip["back"]
    }
    for h in got:
        if h.members[0] == "V1" and h.at.date() in out_days:
            assert h.story.startswith("travel_"), h  # 路上只发生这趟旅行的事
    go = T0.date().fromisoformat(trip["go"])
    day = T0 + timedelta(days=(go - T0.date()).days)
    slot = day.replace(hour=20)
    assert eng.prefer("V1", slot, "lounge", "chat")[0] == AWAY  # 空闲时段不在楼里
    assert eng.prefer("V1", slot.replace(hour=12), "canteen", "eat")[0] == AWAY
    assert eng.prefer("V1", slot.replace(hour=2), "dorm", "sleep") == (AWAY, "sleep")
    # 办公室工作照常：上班时段照作息表
    assert eng.prefer("V1", slot.replace(hour=10), "office", "idle") == ("office", "idle")
    assert eng.travel.away("V1", day.date()) == trip["dest"]


def test_at_most_max_away_are_out_at_once_and_cooldown_holds():
    env = travel_env(max_away=2, cooldown_days=30)
    run_daily(env, 45)
    trips = {m: store.load(env.rt, m).get("trips") or [] for m in NAMES}
    spans = [(t["go"], t["back"]) for ts in trips.values() for t in ts]
    assert spans
    for go, _ in spans:
        out = sum(1 for a, b in spans if a <= go <= b)
        assert out <= 2
    for ts in trips.values():
        for a, b in zip(ts, ts[1:], strict=False):
            gap = (T0.date().fromisoformat(b["planned"]) - T0.date().fromisoformat(a["back"])).days
            assert gap >= 30


def test_destination_follows_what_she_likes():
    picked = []
    for i in range(12):  # 换不同的开始日子，看她挑哪儿
        e = travel_env()
        likes(e, "V2", 最爱=["海边", "沙滩", "日落", "海"])
        run_daily_from(e, i)
        trips = store.load(e.rt, "V2").get("trips") or []
        picked += [t["dest"] for t in trips]
    assert picked.count("海边小镇") >= len(picked) / 4


def run_daily_from(env, offset):
    eng = StoryEngine(env.rt)
    a = T0 + timedelta(days=offset * 31)
    eng.advance(a, a + timedelta(days=1) - timedelta(seconds=1), NAMES)


def test_apartment_shows_her_away_from_life_events():
    env = Env()
    at = T0 + timedelta(hours=20)
    topic = "时间：星期一 20 点 5 分\n地点：外地（去海边小镇旅行了，不在家）"
    rows = [
        {"kind": "solo", "members": ["V1"], "place": "away:V1", "topic": topic},
    ]
    spots = life_spots(rows)
    assert spots["V1"][0] == AWAY and "不在家" in spots["V1"][2]
    vendor = env.config.models.enabled[0].vendor
    b = building(env.config, hour=at.hour, weekday=at.weekday(), life={vendor: spots["V1"]})
    r = next(x for x in b.residents if x.id == vendor)
    assert r.room == AWAY and "旅行" in r.activity_label


def test_travel_never_moves_people_together_and_festivals_skip_the_away():
    env = travel_env()
    run_daily(env, 10)
    st = store.load(env.rt, "V1")
    assert st.get("travel")
    eng = StoryEngine(env.rt)
    # 不在家的人：节日、生日按在家的人过（这里只看 away 判断给得出来）
    go = T0.date().fromisoformat(st["travel"]["go"])
    assert eng.travel.away("V1", go)
    assert not eng.travel.away("V1", go - timedelta(days=1))


def test_the_rest_of_the_library_is_unchanged_when_travel_is_off():
    base = Env().config.roundtable.chat.life.story
    off = story_env(travel=base.travel.model_copy(update={"enabled": False}))
    on = Env()
    a = [h for h in run_daily(off, 20) if not h.story.startswith("travel_")]
    b = run_daily(on, 20)
    out = {m: store.load(on.rt, m).get("trips") or [] for m in NAMES}
    # 旅行有自己的随机数：没出门的人，其余的事和关掉旅行时一模一样
    home = [m for m in NAMES if not out[m]]
    assert home
    assert [(h.at, h.story, h.text) for h in a if h.members[0] in home] == [
        (h.at, h.story, h.text) for h in b if h.members[0] in home
    ]


def test_life_plan_puts_the_traveller_away_and_alone():
    from datetime import UTC, datetime

    from roundtable.core.chat import ChatHub
    from roundtable.core.life import LifeEngine
    from roundtable.core.memory import MemoryKeeper

    env = Env()
    hub = ChatHub(env.rt)
    life = LifeEngine(env.rt, hub, MemoryKeeper(env.rt, hub.everyone))
    member = next(m.id for m in hub.everyone() if m.available)
    day = datetime(2026, 10, 24, 0, 0, tzinfo=UTC)  # 星期六
    st = store.load(env.rt, member)
    st["travel"] = {"dest": "古镇", "planned": "2026-10-21"}
    st["travel"] |= {"go": "2026-10-24", "back": "2026-10-25"}
    store.save(env.rt, member, st)
    plans = life.plan(day + timedelta(hours=9), day + timedelta(hours=21))
    mine = [p for p in plans if member in p.members and p.kind != "pair"]
    assert mine
    for p in mine:
        assert p.kind == "solo" and p.place == f"away:{member}"
        assert "外地（去古镇旅行了，不在家）" in p.topic
