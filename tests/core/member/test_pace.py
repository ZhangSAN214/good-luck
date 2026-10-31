"""回话节拍：等你说完再回、忙时晚回（从这一串第一条算起、刚聊过不晚回、急事不晚回）、
气泡间隔按字数和性格、插话自动引用、收尾不回；私聊里真的等、你接着发就交给新的一轮。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.member.pace import Pace
from roundtable.core.service import RoundtableService

from ..orchestrator.conftest import Env
from .test_lingo import card

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


@pytest.fixture
def env(real_pace):
    env = Env()
    env.rt.budget.now = lambda: NOW
    ChatHub(env.rt).room(dm_room("V1"))
    return env


def say(env, author, text, seconds_ago, room=None):
    at = (NOW - timedelta(seconds=seconds_ago)).isoformat()
    return env.rt.repo.add_chat_message(room or dm_room("V1"), author, text, created_at=at)


def test_debounce_adapts_to_how_fast_you_type(env):
    pace = Pace(env.rt)
    say(env, "user", "在吗", 100)
    assert pace.debounce(dm_room("V1"), NOW) == 1.5  # 只有一条：最短
    say(env, "user", "跟你说个事", 3)
    say(env, "user", "我今天", 0)
    assert pace.debounce(dm_room("V1"), NOW) == pytest.approx(3.9)  # 间隔 3 秒：多等一点
    env2 = Env()
    ChatHub(env2.rt).room(dm_room("V1"))
    for i in range(3):
        say(env2, "user", "啊", 1 - i * 0.5)
    assert Pace(env2.rt).debounce(dm_room("V1"), NOW) == 1.5


def test_busy_members_answer_later_counted_from_your_first_message(env, monkeypatch):
    pace = Pace(env.rt)
    monkeypatch.setattr(Pace, "activity", lambda self, member, at: "exercise")
    say(env, "user", "在干嘛", 30)
    say(env, "user", "理我一下", 0)
    wait = pace.busy_delay("V1", dm_room("V1"), "理我一下", NOW)
    assert 30 <= wait <= 270  # 锻炼 60～300 秒，已经过了 30 秒
    later = pace.busy_delay("V1", dm_room("V1"), "理我一下", NOW + timedelta(seconds=20))
    assert later == pytest.approx(max(0.0, wait - 20))  # 接着发不会越拖越久
    assert pace.busy_delay("V1", dm_room("V1"), "我不想活了", NOW) == 0.0  # 急事
    monkeypatch.setattr(Pace, "activity", lambda self, member, at: "chat")
    assert pace.busy_delay("V1", dm_room("V1"), "在吗", NOW) == 0.0  # 闲着


def test_no_delay_right_after_chatting(env, monkeypatch):
    monkeypatch.setattr(Pace, "activity", lambda self, member, at: "eat")
    say(env, "V1", "我在吃饭呢", 60)
    say(env, "user", "吃的啥", 0)
    assert Pace(env.rt).busy_delay("V1", dm_room("V1"), "吃的啥", NOW) == 0.0


def test_bubble_gap_follows_length_and_typing_speed(env):
    pace = Pace(env.rt)
    short = pace.gap("V2", "好", 1.2)
    long = pace.gap("V2", "我今天下午去图书馆借了三本书，结果回来发现有一本借过了", 1.2)
    assert short == 0.6 and 3.0 < long <= 5.0
    card(env, "V2", (5, 3, 3, 3, 3))
    card(env, "V3", (1, 3, 3, 3, 3))
    text = "我今天下午去图书馆借了三本书"
    assert pace.gap("V2", text, 1.2) < pace.gap("V3", text, 1.2)  # 外向的打字快


def test_quote_when_others_spoke_in_between(env):
    room = dm_room("V1")
    msg = say(env, "user", "你觉得呢", 10)
    pace = Pace(env.rt)
    assert not pace.quote("V1", room, msg["id"])
    say(env, "user", "算了", 5)
    say(env, "user", "当我没说", 2)
    assert pace.quote("V1", room, msg["id"])


def test_wrapup_after_saying_goodnight(env):
    room = dm_room("V1")
    say(env, "user", "我去睡了", 30)
    say(env, "V1", "好～晚安，明天见", 20)
    ack = say(env, "user", "嗯嗯", 0)
    pace = Pace(env.rt)
    assert pace.wrapup("V1", room, ack["id"])
    more = say(env, "user", "对了明天几点", 0)
    assert not pace.wrapup("V1", room, more["id"])  # 不是应声：照回


async def test_dm_waits_and_hands_over_to_the_newer_message(env, monkeypatch):
    """私聊：等你说完时你又发了一条，前一轮不回，交给新的一轮（只回一次）。"""
    svc = RoundtableService(env.rt)
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    chat = svc._chat_room(dm_room("V1"))
    chat.sleep = fake_sleep
    msg, speakers = chat.post("在吗")
    round_no = chat.round
    chat.post("跟你说个事")  # 等的时候你又发了一条
    assert await svc._paced_replies(chat, msg["id"], speakers, round_no) == []
    assert slept and slept[0] >= 1.5
    await svc.aclose()


async def test_dm_does_not_reply_to_a_bare_ack_after_goodbye(env):
    dm = ChatHub(env.rt).room(dm_room("V1"))
    say(env, "user", "我去睡了", 30)
    say(env, "V1", "晚安，明天见", 20)
    before = len(env.fake.calls)
    msg, speakers = dm.post("嗯嗯")
    assert await dm.replies(msg["id"], speakers) == []
    assert len(env.fake.calls) == before
