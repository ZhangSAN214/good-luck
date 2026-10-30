"""10-30 假模型彩排（带八张人物卡）修出的问题：心动不被每天的上限挤掉；承诺要时间；
小毛病按和大家比的性格挑、不会人人一样；档案长不等于什么都懂。"""

from __future__ import annotations

from datetime import timedelta

from roundtable.core.member.human import Human
from roundtable.core.member.love import USER
from roundtable.core.member.profile import ProfileStore

from .test_brain import NOON, TRAITS
from .test_human import world as human_world
from .test_love import world as love_world


def test_spark_still_grows_when_the_daily_cap_is_used_up():
    env, heart, _ = love_world(chemistry_floor=0.0, romance_user=True, daily_cap=0.02)
    for d in range(10):
        for _ in range(5):  # 每天聊很多，日上限一定用满
            heart._touch("V1", USER, NOON + timedelta(days=d), "moment")
    v = heart.view("V1", USER, NOON + timedelta(days=10))
    assert v["spark"] > 0.01 and v["care"] > 0.05


def test_commitment_needs_time_so_a_month_is_not_family_yet():
    env, heart, _ = love_world()
    for d in range(30):
        for _ in range(5):
            heart._touch("V1", "V2", NOON + timedelta(days=d), "moment")
    month = NOON + timedelta(days=30)
    v = heart.view("V1", "V2", month)
    assert v["commitment"] <= 30 / 90 + 0.01
    assert heart.kind(v) not in ("像家人一样", "很深的感情", "心里认定了")
    later = heart.view("V1", "V2", NOON + timedelta(days=120))
    assert later["commitment"] > v["commitment"] - 0.05  # 时间够了，承诺才放开（在乎还会慢慢淡）


def test_flaws_differ_even_when_everyone_is_gentle():
    env, human, _ = human_world()
    store = ProfileStore(env.rt)
    real = [  # 彩排时八张人物卡的性格分（外向、较真、好奇、温和、幽默）
        (2, 5, 5, 4, 3),
        (4, 4, 5, 2, 5),
        (3, 4, 5, 4, 3),
        (2, 3, 4, 4, 3),
        (2, 4, 4, 4, 3),
        (3, 4, 5, 4, 3),
        (3, 3, 5, 4, 4),
        (3, 4, 5, 4, 3),
    ]
    ids = [f"P{i}" for i in range(8)]
    cast = dict(zip(ids, real, strict=True))
    for m, scores in cast.items():
        traits = dict(zip(TRAITS, scores, strict=True))
        store.change(m, {"personality": {"五项": traits}}, author="user")
    human = Human(env.rt, None, {m: m for m in cast})
    picks = [human.flaws(m) for m in cast]
    most = max(sum(name in p for p in picks) for name in {n for p in picks for n in p})
    assert most <= 4, picks  # 改之前八个人里七个都是「善意的谎」


def test_a_long_profile_does_not_make_her_an_expert_in_everything():
    env, human, _ = human_world(extra_domains=0, max_domains=2)
    text = "弹吉他、弹吉他、乐队；下围棋；股票；做饭；画画；看星星"
    ProfileStore(env.rt).change("V1", {"identity": {"爱好": text}}, author="user")
    got = human.domains("V1")
    assert len(got) == 2 and got[0] == "音乐"


def test_friends_can_slowly_fall_for_someone_as_they_grow_closer():
    env, heart, _ = love_world(romance_user=True)
    floor = heart.chemistry("V1", USER) + 0.05  # 底子差一点点，刚认识不会心动
    from .test_love import _love, bond

    _love(env, chemistry_floor=floor, chemistry_grow=0.3)
    heart._touch("V1", USER, NOON, "moment")
    assert heart.view("V1", USER, NOON)["spark"] == 0
    bond(heart, "V1", USER, care=0.6)
    heart._touch("V1", USER, NOON + timedelta(days=1), "moment")
    assert heart.view("V1", USER, NOON + timedelta(days=1))["spark"] > 0
    _love(env, chemistry_floor=floor + 0.5, chemistry_grow=0.3)  # 底子太差的仍然只是朋友
    bond(heart, "V1", USER, care=0.9, spark=0.0)
    heart._touch("V1", USER, NOON + timedelta(days=2), "moment")
    assert heart.view("V1", USER, NOON + timedelta(days=2))["spark"] == 0
