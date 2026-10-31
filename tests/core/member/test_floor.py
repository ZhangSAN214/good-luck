"""群聊分寸：刚说过话的人「在场」、更可能接着聊，没参与的没点名时更容易潜水；每天按外向、幽默分
有话量份额，说超了少说；说多了嘴累；成员说的话远多于你时没点名的一轮只挑一位；关掉不动；
点名的照常回；8 位同一套，只看性格分和今天说了多少。"""

from __future__ import annotations

import random
from collections import Counter
from datetime import timedelta

from roundtable.core.chat import GroupChat
from roundtable.core.member.floor import Floor
from roundtable.core.member.profile import ProfileStore
from roundtable.core.storage.repository import iso

from .test_brain import NOON, TRAITS, setup


def rules(env, **update) -> None:
    cfg = env.rt.config.roundtable
    engine = cfg.chat.engine
    fl = engine.floor.model_copy(update=update)
    chat = cfg.chat.model_copy(update={"engine": engine.model_copy(update={"floor": fl})})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def say(env, author, text="嗯嗯", minutes_ago=0.0, room="main"):
    at = iso(NOON - timedelta(minutes=minutes_ago))
    return env.rt.repo.add_chat_message(room, author, text, created_at=at)


def traits(env, member, outgoing=3, funny=3):
    scores = dict(zip(TRAITS, (outgoing, 3, 3, 3, funny), strict=True))
    ProfileStore(env.rt).change(member, {"personality": {"五项": scores}}, author="user")


def test_present_lasts_longer_for_outgoing_and_counts_being_named():
    env, _ = setup()
    traits(env, "V2", outgoing=1)
    traits(env, "V3", outgoing=5)
    say(env, "V2", minutes_ago=12)
    say(env, "V3", minutes_ago=20)
    say(env, "user", "阿四你呢", minutes_ago=2)
    floor = Floor(env.rt)
    rows = floor.rows("main")
    assert not floor.present("V2", rows, NOON)  # 内向：15 - 8 = 7 分钟就离场了
    assert floor.present("V3", rows, NOON)  # 外向：15 + 8 = 23 分钟
    assert floor.present("V4", rows, NOON, name="阿四")
    assert not floor.present("V5", rows, NOON, name="阿五")


def test_quota_follows_personality_and_going_over_lowers_weight_and_salience():
    env, _ = setup()
    traits(env, "V2", outgoing=1, funny=1)
    traits(env, "V3", outgoing=5, funny=5)
    floor = Floor(env.rt)
    assert floor.quota("V2") < floor.quota("V3")
    rules(env, quota_base=4, quota_min=1)
    for i in range(4):
        say(env, "V2", minutes_ago=200 + i)
    floor = Floor(env.rt)
    rows = floor.rows("main")
    assert floor.today("V2", rows, NOON) == 4
    assert floor.weight("V2", rows, NOON) < floor.weight("V4", rows, NOON)
    assert floor.penalty("V2", "main", NOON) > floor.penalty("V4", "main", NOON)


def test_talking_a_lot_makes_her_tired_and_it_wears_off():
    env, _ = setup()
    for i in range(6):
        say(env, "V2", "我跟你说这个事情真的是太离谱了你听我讲", minutes_ago=i)
    floor = Floor(env.rt)
    rows = floor.rows("main")
    tired = floor.tired("V2", rows, NOON)
    assert tired >= floor.rules.tired_at
    assert floor.tired("V2", rows, NOON + timedelta(hours=3)) < 0.5
    assert floor.tired("V3", rows, NOON) == 0


def test_flooded_when_members_far_outtalk_you():
    env, _ = setup()
    say(env, "user", "早", minutes_ago=100)
    for i in range(25):
        say(env, f"V{i % 6 + 1}", minutes_ago=90 - i)
    floor = Floor(env.rt)
    assert floor.flooded(floor.rows("main"), NOON)
    for i in range(7):
        say(env, "user", "哈哈", minutes_ago=10 + i)
    assert not Floor(env.rt).flooded(Floor(env.rt).rows("main"), NOON)


def test_pick_prefers_people_in_the_conversation_and_only_one_when_flooded():
    env, _ = setup()
    say(env, "V2", minutes_ago=1)
    say(env, "V3", minutes_ago=2)
    counts: Counter = Counter()
    for seed in range(200):
        for m in GroupChat(env.rt, rng=random.Random(seed)).pick("今天吃什么"):
            counts[m.id] += 1
    others = [counts[m] for m in ("V4", "V5", "V6")]
    assert counts["V2"] > max(others) and counts["V3"] > max(others)
    for i in range(25):
        say(env, "V4", minutes_ago=60 + i)
    sizes = {len(GroupChat(env.rt, rng=random.Random(s)).pick("今天吃什么")) for s in range(30)}
    assert sizes == {1}
    assert len(GroupChat(env.rt, rng=random.Random(0)).pick("@阿二 @阿三 你们呢")) == 2  # 点名照常


def test_switched_off_picks_like_before():
    env, _ = setup()
    say(env, "V2", minutes_ago=1)
    rules(env, enabled=False)
    plain = [m.id for m in GroupChat(env.rt, rng=random.Random(3)).pick("今天吃什么")]
    online = [m for m in GroupChat(env.rt).members() if m.available]
    rng = random.Random(3)
    k = rng.randint(1, min(env.rt.config.roundtable.chat.max_repliers, len(online)))
    assert plain == [m.id for m in rng.sample(online, k)]
    assert Floor(env.rt).penalty("V3", "main", NOON) == 0


def test_absent_and_over_quota_make_silence_more_likely_but_named_still_answers():
    env, brain = setup()
    rules(env, absent_penalty=2.0)
    card = brain.intent("V1", last="随便聊聊", named=False, group=True, at=NOON, room="main")
    assert card is not None and not card.silent  # 没人在聊（你开新话题）：不算潜水的理由
    say(env, "V2", minutes_ago=3)
    card = brain.intent("V1", last="随便聊聊", named=False, group=True, at=NOON, room="main")
    assert card is not None and card.silent  # 别人正聊着、她没参与
    say(env, "V1", minutes_ago=1)
    card = brain.intent("V1", last="随便聊聊", named=False, group=True, at=NOON, room="main")
    assert card is not None and not card.silent  # 刚还在聊：在场
    named = brain.intent("V1", last="阿一你说呢", named=True, group=True, at=NOON, room="main")
    assert named is not None and not named.silent
