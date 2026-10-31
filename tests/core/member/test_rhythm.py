"""作息与回话节奏：睡着时多数醒了再回，有时被吵醒（心情不好会抱怨），让人担心的话立刻回；
群里没点名就不回；工作群照常；醒了回你最新那条，太久的不回。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ROOM, ChatHub, dm_room
from roundtable.core.hometz import home_zone
from roundtable.core.member.rhythm import LATER, NOW, WOKEN, Rhythm
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import Env

NIGHT = datetime(2026, 11, 3, 19, 0, tzinfo=UTC)  # 上海 凌晨 3 点（周三）
MORNING = datetime(2026, 11, 4, 2, 0, tzinfo=UTC)  # 上海 上午 10 点


def world(real_rhythm, at=NIGHT, **rules) -> Env:
    env = Env()
    env.rt.budget.now = lambda: at
    env.rt.repo.clock = lambda: iso(at)
    home_zone(env.rt).set("Asia/Shanghai")
    if rules:
        cfg = env.rt.config.roundtable
        rhythm = cfg.chat.engine.rhythm.model_copy(update=rules)
        engine = cfg.chat.engine.model_copy(update={"rhythm": rhythm})
        chat = cfg.chat.model_copy(update={"engine": engine})
        env.rt.config = env.rt.config.model_copy(
            update={"roundtable": cfg.model_copy(update={"chat": chat})}
        )
    return env


def test_members_sleep_at_night_by_the_home_clock(real_rhythm):
    env = world(real_rhythm)
    assert Rhythm(env.rt).asleep("V2", NIGHT) and not Rhythm(env.rt).asleep("V2", MORNING)


async def test_asleep_she_answers_after_waking_up(real_rhythm):
    env = world(real_rhythm, wake_base=0.0, wake_named=0.0, wake_close=0.0, wake_repeat=0.0)
    dm = ChatHub(env.rt).room(dm_room("V2"))
    msg, speakers = dm.post("睡了吗")
    assert speakers == []  # 睡着没看到
    second, _ = dm.post("明天一起吃饭吗")
    (entry,) = Rhythm(env.rt).pending()
    assert entry["msg"] == second["id"] and entry["member"] == "V2"
    assert Rhythm(env.rt).due(NIGHT + timedelta(hours=1)) == []  # 还睡着
    env.rt.budget.now = lambda: MORNING
    env.rt.repo.clock = lambda: iso(MORNING)
    svc = RoundtableService(env.rt)
    assert await svc.member_turns(MORNING) == 1
    prompt = env.fake.calls[-1].messages[-1].content
    assert "你刚醒，才看到对方 3:00 发来的消息" in prompt
    assert Rhythm(env.rt).pending() == []
    replies = [r for r in env.rt.repo.chat_messages(dm.room) if r["author"] == "V2"]
    assert replies  # 醒了回了


def test_old_unseen_messages_are_let_go(real_rhythm):
    env = world(real_rhythm, wake_base=0.0, wake_named=0.0, wake_close=0.0, wake_repeat=0.0)
    rhythm = Rhythm(env.rt)
    rhythm.defer("V2", dm_room("V2"), 1, NIGHT)
    assert rhythm.due(NIGHT + timedelta(hours=40)) == [] and rhythm.pending() == []


async def test_woken_up_she_replies_and_may_grumble_when_in_a_bad_mood(real_rhythm):
    env = world(real_rhythm, wake_base=1.0, grumpy_below=2.0)  # 一定吵醒；心情一定算「不好」
    dm = ChatHub(env.rt).room(dm_room("V2"))
    msg, speakers = dm.post("在吗在吗")
    assert [m.id for m in speakers] == ["V2"]
    await dm.replies(msg["id"], speakers)
    prompt = env.fake.calls[-1].messages[-1].content
    assert "被消息吵醒" in prompt and "大半夜的" in prompt
    env2 = world(real_rhythm, wake_base=1.0, grumpy_below=-2.0)  # 心情再差也不算「不好」
    gate = Rhythm(env2.rt).gate(
        "V2", room=dm_room("V2"), text="在吗", meta=None, named=True, msg_id=1, at=NIGHT
    )
    assert gate.kind == WOKEN and "大半夜" not in gate.note


def test_a_worrying_message_is_answered_right_away(real_rhythm):
    env = world(real_rhythm, wake_base=0.0, wake_named=0.0, wake_close=0.0, wake_repeat=0.0)
    gate = Rhythm(env.rt).gate(
        "V2",
        room=dm_room("V2"),
        text="真的撑不下去了，不想活了",
        meta=None,
        named=True,
        msg_id=7,
        at=NIGHT,
    )
    assert gate.kind == NOW


def test_group_only_keeps_named_messages_and_work_is_untouched(real_rhythm):
    env = world(real_rhythm, wake_base=0.0, wake_named=0.0, wake_close=0.0, wake_repeat=0.0)
    hub = ChatHub(env.rt)
    group = hub.room(ROOM)
    msg, speakers = group.post("有人醒着吗")
    assert speakers == [] and Rhythm(env.rt).pending() == []  # 没点名：不回也不记
    work = hub.room("work")
    _, speakers = work.post("@阿三 进度怎么样")
    assert speakers  # 工作群不看作息


def test_more_likely_woken_by_bursts_and_by_people_she_cares_about(real_rhythm):
    env = world(real_rhythm, wake_base=0.0, wake_named=0.0, wake_close=0.0, wake_repeat=0.3)
    dm = ChatHub(env.rt).room(dm_room("V2"))
    kinds = []
    for i in range(4):
        msg, speakers = dm.post(f"醒醒 {i}")
        kinds.append(bool(speakers))
    assert kinds[0] is False  # 一条不会吵醒（基础概率为 0）
    rhythm = Rhythm(env.rt)
    assert rhythm._burst(dm.room, NIGHT) == 4
    assert rhythm.gate(
        "V2", room=dm.room, text="醒醒", meta=None, named=True, msg_id=99, at=NIGHT
    ).kind in (WOKEN, LATER)
