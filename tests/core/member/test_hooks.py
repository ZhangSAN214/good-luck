"""话头本：你说过带日子的打算，那天过了几天内听到的人可能问一句后来怎样（群里说的只问一次）；
她私聊问你、你隔好久才回说别的，她可能顺口再提；主动找你也把它算成由头。"""

from __future__ import annotations

import random
from datetime import date, timedelta

from roundtable.core.chat import dm_room
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.hooks import Hooks, due_of
from roundtable.core.member.reach import Reach
from roundtable.core.storage.repository import iso

from .test_brain import NOON, setup

MON = date(2026, 11, 2)  # 星期一


def rules(env, **update) -> None:
    cfg = env.rt.config.roundtable
    engine = cfg.chat.engine
    h = engine.hooks.model_copy(update=update)
    chat = cfg.chat.model_copy(update={"engine": engine.model_copy(update={"hooks": h})})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def test_due_of_reads_the_day():
    assert due_of("明天要考试了", MON) == MON + timedelta(days=1)
    assert due_of("后天去体检", MON) == MON + timedelta(days=2)
    assert due_of("下周三有个组会要汇报", MON) == date(2026, 11, 11)
    assert due_of("周五交论文初稿", MON) == date(2026, 11, 6)
    assert due_of("这周末去爬山", MON) == date(2026, 11, 7)
    assert due_of("今晚约了室友吃饭", MON) == MON
    assert due_of("今天好累啊", MON) is None


def test_records_your_plans_not_questions_or_the_work_room():
    env, _ = setup()
    bus = EventBus(env.rt)
    Hooks(env.rt, bus)
    for room, text in (
        (dm_room("V1"), "下周三有个组会要汇报，有点紧张"),
        ("main", "明天你们干嘛？"),
        ("work", "明天要交周报"),
    ):
        row = env.rt.repo.add_chat_message(room, "user", text)
        bus.publish(
            Event("chat.message", NOON, None, {"room": room, "id": row["id"], "from_user": True})
        )
    plans = Hooks(env.rt)._load()["plans"]
    assert [p["quote"] for p in plans] == ["下周三有个组会要汇报，有点紧张"]


def test_asked_once_after_the_day_by_those_who_heard():
    env, brain = setup()
    rules(env, chance=1.0)
    h = Hooks(env.rt)
    h.heard(dm_room("V1"), "明天要考六级了", NOON)
    h.heard("main", "今晚约了室友吃火锅", NOON)
    rng = random.Random(0)
    assert h.line("V1", NOON, rng) is None  # 那天还没到
    later = NOON + timedelta(days=2)
    assert [p["quote"] for p in h.due("V1", later)] == ["明天要考六级了", "今晚约了室友吃火锅"]
    assert [p["quote"] for p in h.due("V2", later)] == ["今晚约了室友吃火锅"]  # 私聊的只有 V1 知道
    assert h.due("V1", NOON + timedelta(days=10)) == []  # 太久了不问
    card = brain.intent("V1", last="今天好累", named=True, group=False, at=later, seed="1")
    assert any(t.startswith("hook:p:") for t in card.tags) and "【话头】" in card.text
    brain.after(card, later)
    left = Hooks(env.rt).due("V1", later)
    assert len(left) == 1  # 问过的那件划掉了
    Hooks(env.rt).after("V2", [f"hook:p:{left[0]['id']}"])
    assert Hooks(env.rt).due("V1", later) == []  # 群里说的有人问过，别人就不再问


def test_she_brings_back_the_question_you_left_hanging():
    env, _ = setup()
    rules(env, q_chance=1.0)
    room = dm_room("V1")
    q = env.rt.repo.add_chat_message(room, "V1", "那你周末去哪玩？", created_at=iso(NOON))
    later = NOON + timedelta(hours=6)
    env.rt.repo.add_chat_message(room, "user", "今天好累", created_at=iso(later))
    got = Hooks(env.rt).line("V1", later, random.Random(0), room)
    assert got == (f"hook:q:{q['id']}", Hooks(env.rt).rules.q_line.format(quote="那你周末去哪玩？"))
    Hooks(env.rt).after("V1", [got[0]])
    assert Hooks(env.rt).line("V1", later, random.Random(0), room) is None  # 只提一次


def test_a_quick_answer_is_not_hanging():
    env, _ = setup()
    rules(env, q_chance=1.0)
    room = dm_room("V1")
    env.rt.repo.add_chat_message(room, "V1", "那你周末去哪玩？", created_at=iso(NOON))
    env.rt.repo.add_chat_message(
        room, "user", "还没想好", created_at=iso(NOON + timedelta(minutes=3))
    )
    assert Hooks(env.rt).line("V1", NOON, random.Random(0), room) is None


def test_reach_counts_a_passed_plan_as_a_reason_and_crosses_it_off():
    env, _ = setup()
    h = Hooks(env.rt)
    h.heard(dm_room("V1"), "明天要考六级了", NOON)
    later = NOON + timedelta(days=2)
    reach = Reach(env.rt)
    urge = reach.urge("V1", dm_room("V1"), later)
    assert urge is not None and urge.reason == "hook" and "明天要考六级了" in urge.note
    reach.sent(urge, 1, later)
    assert Hooks(env.rt).due("V1", later) == []


def test_switched_off():
    env, _ = setup()
    rules(env, enabled=False)
    assert not Hooks(env.rt).heard("main", "明天要考六级了", NOON)
