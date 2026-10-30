"""爱与复合情绪：在乎从相处里慢慢长、可以不对称、每天有上限；心动看化学反应和开关；
安慰换来感激，当面反驳留下委屈和内疚、承诺高的原谅得快；焦虑型比回避型想得多；
对用户：只留一次、说睡觉上课时痛快放人、怪没来只一两句、从不记委屈、吃醋只对同事；
替对方高兴、各人的表达方式不同；工作群不算；接进意图卡，关心时段压过一切。"""

from __future__ import annotations

import random
from datetime import timedelta

from roundtable.core.member.brain import Brain, IntentCard, Thought
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.love import USER, Heart
from roundtable.core.member.profile import ProfileStore
from roundtable.core.storage.repository import iso

from .test_brain import NOON, TRAITS, setup

PAIR = "pair:V1:V2"
NAMES = {"V1": "小一", "V2": "小二", "V3": "小三"}


def _love(env, **update) -> None:
    """改 love 的几项配置（测试用）。"""
    cfg = env.rt.config.roundtable
    love = cfg.chat.engine.brain.love.model_copy(update=update)
    brain = cfg.chat.engine.brain.model_copy(update={"love": love})
    engine = cfg.chat.engine.model_copy(update={"brain": brain})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def world(**love):
    env, _ = setup()
    for m, scores in (("V2", (3, 3, 3, 4, 3)), ("V3", (2, 4, 3, 3, 3))):
        traits = dict(zip(TRAITS, scores, strict=True))
        ProfileStore(env.rt).change(m, {"personality": {"五项": traits}}, author="user")
    if love:
        _love(env, **love)
    repo = env.rt.repo
    repo.ensure_chat_room(PAIR, kind="life", title="", members=["V1", "V2"], with_user=False)
    repo.ensure_chat_room("dm:V1", kind="dm", title="", members=["V1"])
    bus = EventBus(env.rt)
    return env, Heart(env.rt, bus, NAMES), bus


def say(env, bus, room, author, text, at):
    row = env.rt.repo.add_chat_message(room, author, text, created_at=iso(at))
    who = None if author == "user" else author
    data = {"room": room, "id": row["id"], "from_user": author == "user"}
    bus.publish(Event("chat.message", at, who, data))


def bond(heart, member, other, at=NOON, **fields):
    data = heart._load(member)
    b = heart._bond(data, other)
    b.update({"at": iso(at), "seen": iso(at)} | fields)
    heart._save(member, data)


def card(member, intent, to, tags=(), spotlight=None):
    return IntentCard(member, intent, "…", spotlight, 1.0, tuple(tags), to=to)


# --- 在乎、心动 ---------------------------------------------------------------------


def test_care_grows_from_exchanges_is_asymmetric_and_capped_per_day():
    env, heart, bus = world()
    at = NOON
    for i in range(3):
        say(env, bus, PAIR, "V1", "今天吃什么？", at + timedelta(minutes=2 * i))
        say(env, bus, PAIR, "V2", "食堂吧", at + timedelta(minutes=2 * i + 1))
    v2 = heart.view("V2", "V1", at)["care"]
    v1 = heart.view("V1", "V2", at)["care"]
    assert v1 > 0 and v2 > 0 and v1 != v2  # 被接住和接话涨得不一样
    for i in range(200):
        t = at + timedelta(minutes=10 + i)
        say(env, bus, PAIR, "V1" if i % 2 else "V2", "嗯", t)
    end = at + timedelta(minutes=300)
    assert (
        heart.view("V1", "V2", end)["care"]
        <= env.rt.config.roundtable.chat.engine.brain.love.daily_cap + 1e-6
    )
    later = end + timedelta(days=60)
    assert heart.view("V1", "V2", later)["care"] < heart.view("V1", "V2", end)["care"]  # 不来往会淡


def test_user_exchanges_count_for_the_member_but_user_has_no_state():
    env, heart, bus = world()
    say(env, bus, "dm:V1", "user", "在吗？", NOON)
    say(env, bus, "dm:V1", "V1", "在呀", NOON + timedelta(minutes=1))
    assert heart.view("V1", USER, NOON)["care"] > 0
    assert env.rt.repo.setting("member_heart:user") is None


def test_spark_needs_the_switch_and_enough_chemistry():
    env, heart, bus = world(chemistry_floor=0.0)
    for d in range(10):
        bond(
            heart,
            "V1",
            USER,
            NOON + timedelta(days=d),
            care=heart.view("V1", USER, NOON).get("care", 0),
        )
        heart._touch("V1", USER, NOON + timedelta(days=d), "moment")
    assert heart.view("V1", USER, NOON + timedelta(days=10))["spark"] > 0
    _love(env, romance_user=False)
    heart._touch("V2", USER, NOON, "moment")
    assert heart.view("V2", USER, NOON)["spark"] == 0
    _love(env, romance_user=True, chemistry_floor=1.0)
    heart._touch("V3", USER, NOON, "moment")
    assert heart.view("V3", USER, NOON)["spark"] == 0


def test_chemistry_is_directional_so_a_crush_can_be_one_sided():
    env, heart, _ = world()
    pairs = [(f"M{i}", f"M{j}") for i in range(8) for j in range(8) if i != j]
    assert any(heart.chemistry(a, b) != heart.chemistry(b, a) for a, b in pairs)
    assert all(0 <= heart.chemistry(a, b) <= 1 for a, b in pairs)
    assert heart.chemistry("M1", "M2") == heart.chemistry("M1", "M2")  # 可复现


# --- 会过去的感情 --------------------------------------------------------------------


def test_comfort_leaves_gratitude_and_shows_on_the_card():
    env, heart, bus = world()
    say(env, bus, PAIR, "V1", "今天好累，有点难过", NOON)
    say(env, bus, PAIR, "V2", "抱抱，辛苦了", NOON + timedelta(minutes=2))
    v = heart.view("V1", "V2", NOON + timedelta(minutes=3))
    assert v["grateful"] > 0.25
    lines = {heart.line("V1", "V2", NOON, random.Random(i)) for i in range(30)}
    assert any(line and line[0] == "grateful" and "小二安慰过你" in line[1] for line in lines)


def test_disagree_hurts_the_listener_and_the_speaker_feels_guilty_if_they_care():
    env, heart, _ = world()
    bond(heart, "V1", "V2", care=0.6)
    heart.after(card("V1", "disagree", "V2"), NOON)
    hurt = heart.view("V2", "V1", NOON)
    assert hurt["hurt"] > 0 and hurt["dislike"] > 0
    assert heart.view("V1", "V2", NOON)["guilt"] > 0
    heart.after(card("V3", "disagree", "V2"), NOON)
    assert not heart.view("V3", "V2", NOON).get("guilt")  # 不在乎就不内疚


def test_high_commitment_forgives_faster():
    env, heart, _ = world()
    for m, care in (("V1", 0.9), ("V3", 0.0)):
        bond(heart, "V2", m, care=care, trust=0.9 if care else 0.1)
        heart._feel("V2", m, "hurt", 0.5, "{name}当面反驳过你", NOON)
    later = NOON + timedelta(days=2)
    assert heart.view("V2", "V1", later)["hurt"] < heart.view("V2", "V3", later)["hurt"]


def test_missed_bids_hurt_between_members_but_never_toward_the_user():
    env, heart, bus = world()
    say(env, bus, PAIR, "V1", "你在忙吗？", NOON)
    say(env, bus, "dm:V1", "V1", "你在忙吗？", NOON)
    assert heart.tick(NOON + timedelta(hours=2)) == 1
    assert heart.view("V1", "V2", NOON + timedelta(hours=2))["hurt"] > 0
    assert not heart.view("V1", USER, NOON + timedelta(hours=2)).get("hurt")
    assert heart.tick(NOON + timedelta(hours=3)) == 0  # 只算一次


# --- 复合情绪 ---------------------------------------------------------------------


def test_longing_grows_faster_for_anxious_than_avoidant():
    env, heart, _ = world()
    bond(heart, "V1", "V2", care=0.7)
    later = NOON + timedelta(days=4)
    v = heart.view("V1", "V2", later)
    heart.attachment = lambda m: "anxious"
    anxious = heart.longing("V1", v, later)
    thoughts = heart.thoughts("V1", later, present={"V1"})
    heart.attachment = lambda m: "avoidant"
    avoidant = heart.longing("V1", v, later)
    assert anxious > avoidant > 0
    assert any("想" in t and "小二" in t for t, _ in thoughts)
    assert not heart.thoughts("V1", later, present={"V1", "V2"})  # 在场就不想念


def test_news_about_oneself_makes_those_who_care_feel_for_them():
    env, heart, _ = world()
    bond(heart, "V2", "V1", care=0.5)
    spot = Thought("goal", "练琴的事又没什么进展", 0.7)
    heart.after(card("V1", "share", "V2", spotlight=spot), NOON)
    texts = [t for t, _ in heart.thoughts("V2", NOON + timedelta(hours=1), present={"V2", "V1"})]
    assert any("替小一揪心" in t for t in texts)


def test_kinds_follow_the_love_triangle():
    env, heart, _ = world()
    base = {"intimacy": 0.0, "spark": 0.0, "commitment": 0.0}
    assert heart.kind(base | {"intimacy": 0.8, "commitment": 0.8}) == "像家人一样"
    assert heart.kind(base | {"spark": 0.8}) == "有点心动"
    assert heart.kind(base) == ""


def test_styles_and_attachment_follow_personality():
    env, heart, _ = world()
    store = ProfileStore(env.rt)
    for m, scores in (("A", (5, 3, 3, 3, 1)), ("B", (1, 3, 3, 1, 3)), ("C", (3, 3, 3, 5, 3))):
        store.change(
            m, {"personality": {"五项": dict(zip(TRAITS, scores, strict=True))}}, author="user"
        )
    assert heart.style("A") == "words"
    assert heart.style("B") == "acts" and heart.attachment("B") == "avoidant"
    assert heart.style("C") == "tending"
    assert len(heart.privileges("A")) == 2


# --- 对用户的边界 -------------------------------------------------------------------


def test_lets_go_when_the_user_goes_to_sleep_or_class_even_if_she_cares_a_lot():
    env, heart, _ = world()
    bond(heart, "V1", USER, care=0.9, spark=0.8)
    for text in ("困了，我去睡了", "要上课了先走了", "约了朋友，先撤"):
        tag, line = heart.line("V1", USER, NOON, random.Random(0), last=text)
        assert tag == "let_go" and "别留" in line


def test_retains_only_once_a_day_and_only_when_she_cares():
    env, heart, _ = world()
    bond(heart, "V1", USER, care=0.7)
    tag, line = heart.line("V1", USER, NOON, random.Random(1), last="先走了")
    picks = {heart.line("V1", USER, NOON, random.Random(i), last="先走了")[0] for i in range(20)}
    assert "retain" in picks
    retain = next(
        heart.line("V1", USER, NOON, random.Random(i), last="先走了")
        for i in range(20)
        if heart.line("V1", USER, NOON, random.Random(i), last="先走了")[0] == "retain"
    )
    assert "只留这一次" in retain[1] and "坚持就放手" in retain[1]
    heart.after(card("V1", "respond", USER, tags=("love:retain",)), NOON)
    again = {heart.line("V1", USER, NOON, random.Random(i), last="先走了") for i in range(20)}
    assert all(x is None or x[0] != "retain" for x in again)
    bond(heart, "V2", USER, care=0.2)
    assert heart.line("V2", USER, NOON, random.Random(0), last="先走了") is None
    _love(env, retain_blame=False)
    lines = {
        heart.line("V1", USER, NOON + timedelta(days=1), random.Random(i), last="先走了")
        for i in range(20)
    }
    assert all(x is None or x[0] not in ("retain", "let_go") for x in lines)


def test_blames_a_long_absence_lightly_and_only_once():
    env, heart, _ = world()
    bond(heart, "V1", USER, care=0.6)
    later = NOON + timedelta(days=3)
    lines = [heart.line("V1", USER, later, random.Random(i), last="我回来啦") for i in range(30)]
    blames = [x for x in lines if x and x[0] == "blame"]
    assert blames and "3天" in blames[0][1] and "不翻旧账" in blames[0][1]
    heart.after(card("V1", "respond", USER, tags=("love:blame",)), later)
    lines = [heart.line("V1", USER, later, random.Random(i), last="我回来啦") for i in range(30)]
    assert all(x is None or x[0] != "blame" for x in lines)


def test_never_hurt_or_guilty_toward_the_user_and_jealous_only_of_members():
    env, heart, _ = world()
    bond(heart, "V1", USER, care=0.6)
    heart.after(card("V1", "disagree", USER), NOON)
    assert not heart.view("V1", USER, NOON).get("guilt")
    assert heart.rival("V1", USER, NOON) in ("", "V2", "V3")
    heart.rival = lambda m, o, at: "V2"
    lines = [heart.line("V1", USER, NOON, random.Random(i)) for i in range(60)]
    jealous = [x[1] for x in lines if x and x[0] == "jealous"]
    assert jealous and all("别要求什么" in t and "小二" in t for t in jealous)


# --- 中立、工作群、接进意图卡 ----------------------------------------------------------


def test_same_rules_for_everyone_only_ids_and_profiles_differ():
    env, heart, _ = world()
    store = ProfileStore(env.rt)
    for m in ("X", "Y"):
        store.change(m, {"personality": {"五项": dict.fromkeys(TRAITS, 3)}}, author="user")
        bond(heart, m, USER, care=0.6)
    vx, vy = heart.view("X", USER, NOON), heart.view("Y", USER, NOON)
    assert vx["care"] == vy["care"] and heart.kind(vx) == heart.kind(vy)


def test_work_rooms_do_not_count():
    env, heart, bus = world()
    env.rt.repo.ensure_chat_room("work", kind="work", title="", members=["V1", "V2"])
    say(env, bus, "work", "V1", "这个方案你看下？", NOON)
    say(env, bus, "work", "V2", "好的", NOON + timedelta(minutes=1))
    assert heart.view("V2", "V1", NOON) == {}


def test_brain_card_carries_the_love_line_and_crisis_care_overrides_it():
    env, heart, _ = world()
    bond(heart, "V1", USER, care=0.9)
    brain = Brain(env.rt, names=NAMES)
    c = brain.intent("V1", last="困了，我去睡了", group=False, at=NOON, room="dm:V1")
    assert c.to == USER and "love:let_go" in c.tags and "别留" in c.text
    crisis = brain.intent("V1", last="我真的不想活了", group=False, at=NOON, room="dm:V1")
    assert crisis.intent == "care" and "别留" not in crisis.text
    brain.after(crisis, NOON)
    assert heart.view("V1", USER, NOON).get("retained", "") == ""


def test_caring_about_the_speaker_makes_their_words_more_salient():
    env, heart, _ = world()
    brain = Brain(env.rt, names=NAMES)
    plain = brain.intent("V2", last="今天去了趟公园", speaker="V3", at=NOON, seed="a")
    bond(heart, "V2", "V3", care=0.9)
    loved = brain.intent("V2", last="今天去了趟公园", speaker="V3", at=NOON, seed="a")
    assert loved.salience > plain.salience
