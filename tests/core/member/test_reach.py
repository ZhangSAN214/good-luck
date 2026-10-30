"""主动找你：有真事由（心情、新鲜事、挂着的事、想念）才先开口；上次没回不追、隔几天、你刚聊过不找、
你的深夜不找、全体每天有上限；开口那一句走私聊同一套提示词。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.hometz import home_zone
from roundtable.core.member.reach import Reach
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import Env

AFTERNOON = datetime(2026, 11, 4, 7, 0, tzinfo=UTC)  # 上海 下午 3 点


def world(monkeypatch, at=AFTERNOON, mood=-0.6, care=0.6, **rules) -> Env:
    env = Env()
    env.rt.budget.now = lambda: at
    env.rt.repo.clock = lambda: iso(at)
    home_zone(env.rt).set("Asia/Shanghai")
    cfg = env.rt.config.roundtable
    reach = cfg.chat.engine.reach.model_copy(update={"per_day_chance": 500.0, **rules})
    engine = cfg.chat.engine.model_copy(update={"reach": reach})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )
    monkeypatch.setattr(Reach, "_mood", lambda self, member, at: mood)
    monkeypatch.setattr(Reach, "_care", lambda self, member, at: care)
    monkeypatch.setattr(Reach, "_news", lambda self, member, at: None)
    return env


def ids(env) -> list[str]:
    return sorted(m.id for m in ChatHub(env.rt).everyone() if m.available)


async def test_in_a_low_mood_she_reaches_out_first_once(monkeypatch):
    env = world(monkeypatch)
    svc = RoundtableService(env.rt)
    assert await svc._reach_out(AFTERNOON) == 1
    prompt = env.fake.calls[-1].messages[-1].content
    assert "心情不太好，想找对方说说话" in prompt and "主动找对方" in prompt
    (member,) = [m for m, v in Reach(env.rt)._load()["members"].items()]
    room = dm_room(member)
    said = [r for r in env.rt.repo.chat_messages(room) if r["author"] == member]
    assert said
    # 你没回：她不再找（不追）；两天后也不找
    later = AFTERNOON + timedelta(days=3)
    assert Reach(env.rt).blocked(member, room, later)
    env.rt.repo.clock = lambda: iso(AFTERNOON + timedelta(hours=1))
    ChatHub(env.rt).room(room).post("刚看到，怎么啦")
    assert not Reach(env.rt).blocked(member, room, later)


def test_no_reason_no_message(monkeypatch):
    env = world(monkeypatch, mood=0.0, care=0.1)
    assert Reach(env.rt).pick(ids(env), dm_room, AFTERNOON) is None


def test_a_good_mood_shares_even_without_much_closeness(monkeypatch):
    env = world(monkeypatch, mood=0.8, care=0.0)
    urge = Reach(env.rt).pick(ids(env), dm_room, AFTERNOON)
    assert urge is not None and urge.reason == "mood_high"


def test_something_she_has_been_wondering_about(monkeypatch):
    env = world(monkeypatch, mood=0.0, care=0.0)
    env.rt.repo.add_memory_hanging("V2", "张三下周三组会汇报", "学业", iso(AFTERNOON))
    urge = Reach(env.rt).urge("V2", dm_room("V2"), AFTERNOON)
    assert urge.reason == "hanging" and "组会" in urge.note


def test_missing_you_after_days_apart(monkeypatch):
    env = world(monkeypatch, mood=0.0, care=0.8)
    room = dm_room("V2")
    env.rt.repo.clock = lambda: iso(AFTERNOON - timedelta(days=5))
    ChatHub(env.rt).room(room).post("最近好忙")
    urge = Reach(env.rt).urge("V2", room, AFTERNOON)
    assert urge.reason == "longing"


def test_never_in_your_late_night_nor_right_after_you_talked(monkeypatch):
    night = datetime(2026, 11, 4, 16, 30, tzinfo=UTC)  # 上海 00:30
    env = world(monkeypatch, at=night)
    assert Reach(env.rt).pick(ids(env), dm_room, night) is None
    env = world(monkeypatch)
    room = dm_room("V2")
    ChatHub(env.rt).room(room).post("下午好")  # 你刚聊过
    assert Reach(env.rt).blocked("V2", room, AFTERNOON + timedelta(hours=1))


def test_a_daily_cap_for_everyone(monkeypatch):
    env = world(monkeypatch, per_day=1)
    reach = Reach(env.rt)
    first = reach.pick(ids(env), dm_room, AFTERNOON)
    reach.sent(first, 999, AFTERNOON)
    assert reach.pick(ids(env), dm_room, AFTERNOON + timedelta(minutes=30)) is None
