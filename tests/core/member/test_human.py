"""人味：不熟的领域说不知道；只挑一点回、忙自己的、聊久了自己撤；记得自己说过的话和答应的事；
会聊不在场的人；想起从前和梦想；按性格有小毛病；主动约人、做不下去就放下；大事前紧张；工作群不算。"""

from __future__ import annotations

import random
from datetime import timedelta

from roundtable.core.member.brain import Brain, IntentCard, Thought
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.human import Human
from roundtable.core.member.love import USER
from roundtable.core.member.profile import ProfileStore
from roundtable.core.office.apartment import schedule
from roundtable.core.storage.repository import iso

from .test_brain import NOON, TRAITS, setup
from .test_love import NAMES, bond

DM = "dm:V1"


def _human(env, **update) -> None:
    cfg = env.rt.config.roundtable
    human = cfg.chat.engine.brain.human.model_copy(update=update)
    brain = cfg.chat.engine.brain.model_copy(update={"human": human})
    engine = cfg.chat.engine.model_copy(update={"brain": brain})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def world(**update):
    env, _ = setup()  # V1 喜欢弹吉他、下围棋
    if update:
        _human(env, **update)
    env.rt.repo.ensure_chat_room(DM, kind="dm", title="", members=["V1"])
    bus = EventBus(env.rt)
    return env, Human(env.rt, bus, NAMES), bus


def say(env, bus, room, author, text, at):
    row = env.rt.repo.add_chat_message(room, author, text, created_at=iso(at))
    who = None if author == "user" else author
    data = {"room": room, "id": row["id"], "from_user": author == "user"}
    bus.publish(Event("chat.message", at, who, data))


def lines(human, rng_seeds=range(1), **kw):
    base = {
        "to": USER,
        "last": "",
        "chosen": "respond",
        "room": "",
        "energy": 0.6,
        "social": 0.3,
        "spotlight": None,
        "hurt": False,
        "at": NOON,
    }
    out = []
    for i in rng_seeds:
        out += human.lines("V1", **(base | kw), rng=random.Random(i))
    return out


# --- 知识边界 ---------------------------------------------------------------------


def test_unfamiliar_domains_get_an_honest_i_dont_know():
    env, human, _ = world(extra_domains=0)
    assert "音乐" in human.domains("V1")  # 档案里提到弹吉他
    got = dict(lines(human, last="最近股票能买吗？"))
    assert "unknown" in got and "金融" in got["unknown"] and "猜" in got["unknown"]
    assert "unknown" not in dict(lines(human, last="吉他和弦怎么按？"))
    assert "unknown" not in dict(lines(human, last="我买了点股票"))  # 不是问她
    assert "unknown" not in dict(lines(human, last="晚上一起打游戏吗？"))  # 邀约不是问知识


def test_extra_domains_differ_by_member_but_follow_one_rule():
    env, human, _ = world()
    store = ProfileStore(env.rt)
    for m in ("X", "Y"):
        store.change(m, {"personality": {"五项": dict.fromkeys(TRAITS, 3)}}, author="user")
    picks = {tuple(human.domains(f"M{i}")) for i in range(8)}
    assert len(picks) > 1 and all(len(p) == 1 for p in picks)


# --- 注意力和自己的时间 -------------------------------------------------------------


def test_long_messages_get_one_point_answered():
    env, human, _ = world()
    long = "今天先是去了图书馆，然后下午考了试，晚上又去吃了火锅，回来还看了部电影，" * 2
    assert any(t == "attention" for t, _ in lines(human, range(10), last=long))


def test_busy_with_her_own_life_and_wraps_up_long_tiring_chats():
    env, human, _ = world(busy_chance=1.0, wrap_chance=1.0)
    sleep_hour = next(h for h in range(24) if schedule("V1", h, NOON.weekday())[1] == "sleep")
    from roundtable.core.hometz import home_zone

    home_zone(env.rt).set("UTC")
    at = NOON.replace(hour=sleep_hour)
    assert dict(lines(human, room=DM, at=at)).get("busy", "").startswith("你这会儿在睡觉")
    free = next(h for h in range(24) if schedule("V1", h, NOON.weekday())[1] in ("idle", "work"))
    at = NOON.replace(hour=free)
    for i in range(9):
        env.rt.repo.add_chat_message(DM, "V1", "嗯嗯", created_at=iso(at - timedelta(minutes=i)))
    assert "wrap" in dict(lines(human, room=DM, at=at, energy=0.2))
    assert "wrap" not in dict(lines(human, room=DM, at=at, energy=0.8))


# --- 自我一致 ---------------------------------------------------------------------


def test_remembers_what_she_said_about_herself_and_her_promises():
    env, human, bus = world()
    say(env, bus, DM, "V1", "我从来不吃香菜，下次我请你吃火锅", NOON)
    got = dict(lines(human, last="香菜到底好不好吃？"))
    assert "从来不吃香菜" in got["consistent"]
    thoughts = [t for t, _ in human.thoughts("V1", NOON, {"V1", USER}, random.Random(0))]
    assert any("下次我请你吃火锅" in t and "用户" in t for t in thoughts)
    later = NOON + timedelta(days=8)
    assert not any("火锅" in t for t, _ in human.thoughts("V1", later, {"V1"}, random.Random(0)))


def test_work_room_messages_are_not_remembered():
    env, human, bus = world()
    env.rt.repo.ensure_chat_room("work", kind="work", title="", members=["V1"])
    say(env, bus, "work", "V1", "我从来不用这个库", NOON)
    assert human._load("V1")["claims"] == []


# --- 八卦、时间纵深 -----------------------------------------------------------------


def test_thinks_about_absent_people_and_vents_about_those_she_dislikes():
    env, human, _ = world(gossip_chance=1.0)
    texts = [t for t, _ in human.thoughts("V1", NOON, {"V1", "V2", "V3"}, random.Random(0))]
    assert not any("聊聊" in t or "吐槽" in t for t in texts)  # 都在场就不八卦
    from roundtable.core.member.love import Heart

    heart = Heart(env.rt, names=NAMES)
    for m in ("V2", "V3"):
        bond(heart, "V1", m, dislike=0.6, dislike_at=iso(NOON))
    texts = [t for t, _ in human.thoughts("V1", NOON, {"V1"}, random.Random(0))]
    assert any("吐槽" in t for t in texts)


def test_remembers_her_past_and_dreams_from_the_profile():
    env, human, _ = world(depth_chance=1.0)
    ProfileStore(env.rt).change("V1", {"identity": {"身世": "小时候在海边长大"}}, author="user")
    texts = [t for t, _ in human.thoughts("V1", NOON, {"V1"}, random.Random(0))]
    assert "想起小时候在海边长大" in texts


# --- 小毛病 -----------------------------------------------------------------------


def test_flaws_follow_personality_and_only_show_in_their_situation():
    env, human, _ = world()
    store = ProfileStore(env.rt)
    sulky = dict(zip(TRAITS, (1, 3, 3, 1, 3), strict=True))
    store.change("V1", {"personality": {"五项": sulky}}, author="user")
    mine = human.flaws("V1")
    assert len(mine) == 2 and set(mine) & {"生闷气", "记仇"}
    calm = [t for t, _ in lines(human, range(30), hurt=False)]
    hurt = [t for t, _ in lines(human, range(30), hurt=True)]
    assert not any(t.startswith("flaw:") and t[5:] in ("生闷气", "记仇") for t in calm)
    assert any(t.startswith("flaw:") for t in hurt)


# --- 主动做决定 ---------------------------------------------------------------------


def test_proposes_plans_and_remembers_them_once_said():
    env, human, _ = world(propose_chance=1.0)
    got = dict(lines(human, social=0.9))
    assert got["propose"].startswith("你想约用户")
    card = IntentCard("V1", "share", "此刻\n" + got["propose"], None, 1.0, ("propose",), to=USER)
    human.after(card, NOON)
    texts = [t for t, _ in human.thoughts("V1", NOON, {"V1"}, random.Random(0))]
    assert any(t.startswith("约了用户") for t in texts)
    assert "propose" not in dict(lines(human, social=0.2))


def test_gives_up_a_stuck_goal_and_picks_another():
    env, brain = setup()
    cfg = env.rt.config.roundtable.chat.engine.brain.goals
    goals = cfg.model_copy(update={"setback_chance": 1.0, "give_up_chance": 1.0})
    from .test_detemplate import _brain

    _brain(env, goals=goals)
    s = brain._load("V1")
    s.goals = [{"name": "练琴", "xp": 1.0, "level": 0, "stuck": 0, "fails": 2}]
    note = brain._pursue(s, s.goals[0], random.Random(0), NOON)
    assert note.startswith("决定先放下「练琴」") and s.goals[0]["name"] != "练琴"
    assert s.dropped[0]["name"] == "练琴" and any("放下" in n["text"] for n in s.news)


def test_gets_nervous_right_before_a_big_moment():
    env, brain = setup()
    s = brain._load("V1")
    s.expects = [
        {"text": "盼着周末去爬山", "due": iso(NOON + timedelta(hours=10)), "at": iso(NOON)}
    ]
    texts = [t.text for t in brain.thoughts("V1", s, NOON, random.Random(0))]
    assert "盼着周末去爬山，快见分晓了，有点紧张" in texts


# --- 接进意图卡 -------------------------------------------------------------------


def test_the_honest_i_dont_know_comes_first_on_the_card():
    env, brain = setup()
    _human(env, extra_domains=0)
    from .test_detemplate import _brain

    _brain(env, max_cues=1)
    card = Brain(env.rt, names=NAMES).intent(
        "V1", last="最近股票能买吗？", group=False, at=NOON, room=DM
    )
    assert "unknown" in card.tags and "不太懂" in card.text
    assert isinstance(card.spotlight, Thought | None)
