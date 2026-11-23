"""游戏区总管：开桌扣金币、离桌退回；成员用模型出牌（同一份提示词、只挑合法编号）、
答非所问 / 到每天上限时本地电脑接手；旁观押注的结算；每天送金币。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import pytest

from roundtable.core.arcade import Arcade, ArcadeError
from roundtable.core.arcade.hub import STEP, YOU, option_lines, parse_pick
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import Env

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)


async def _no_sleep(_s: float) -> None:
    return None


def make(env: Env | None = None, **rules) -> tuple[Env, Arcade]:
    env = env or Env()
    env.rt.budget.now = lambda: NOW
    env.rt.repo.clock = lambda: iso(NOW)
    if rules:
        cfg = env.rt.config.roundtable
        arcade = cfg.arcade.model_copy(update=rules)
        env.rt.config = env.rt.config.model_copy(
            update={"roundtable": cfg.model_copy(update={"arcade": arcade})}
        )
    from roundtable.core.chat import ChatHub

    hub = ChatHub(env.rt)
    import random

    return env, Arcade(env.rt, hub.everyone, rng=random.Random(1), sleep=_no_sleep)


def ids(arcade: Arcade, n: int) -> list[str]:
    return [m.id for m in arcade.everyone()][:n]


def play_out(arcade: Arcade) -> None:
    """你一直过牌 / 跟注，成员按各自的方式出，直到这一手结束。"""
    for _ in range(200):
        asyncio.run(arcade.drive())
        v = arcade.view()
        if v["street"] == "done":
            return
        kinds = [o["kind"] for o in v["options"]]
        arcade.act("check" if "check" in kinds else ("call" if "call" in kinds else "allin"))
    raise AssertionError("这一手没打完")


def test_wallet_start_and_daily_gift():
    env, arcade = make()
    assert arcade.status()["wallet"]["coins"] == 2000
    arcade.rt.budget.now = lambda: NOW.replace(day=24)
    w = arcade.status()["wallet"]
    assert w["coins"] == 2500 and w["gifted"] == 500
    assert arcade.status()["wallet"]["gifted"] == 0  # 同一天只送一次


def test_sit_buys_in_and_leave_returns_chips():
    env, arcade = make()
    v = arcade.open_holdem(ids(arcade, 2), sit=True, think=False)
    assert v["seated"] and len(v["seats"]) == 3
    assert arcade.wallet()["coins"] == 1000  # 带了 1000 上桌
    play_out(arcade)
    stack = next(s["stack"] for s in arcade.view()["seats"] if s["id"] == YOU)
    out = arcade.leave()
    assert out["wallet"]["coins"] == 1000 + stack
    assert arcade.table is None


def test_members_think_with_their_own_model_and_same_prompt():
    env, arcade = make()
    arcade.open_holdem(ids(arcade, 3), sit=True, think=True)
    play_out(arcade)
    calls = [c for c in env.fake.calls if "打一桌德州扑克" in c.messages[0].content]
    assert calls, "成员应该用模型出过牌"
    roster = {m.id: m.model_id for m in arcade.everyone()}
    assert {c.model for c in calls} <= set(roster.values())
    # 中立：所有成员的系统提示只差名字，参数完全相同
    params = {json.dumps(c.params, sort_keys=True) for c in calls}
    assert len(params) == 1
    brains = {s["brain"] for s in arcade.view()["seats"] if s["id"] != YOU and s["brain"]}
    assert "model" in brains
    assert any(s["say"] == "跟一手看看" for s in arcade.view()["seats"])


def test_bad_answer_falls_back_to_local():
    env = Env()
    env.fake._default = lambda model, messages: "我也不知道"
    env, arcade = make(env)
    arcade.open_holdem(ids(arcade, 2), sit=True, think=True)
    play_out(arcade)
    assert arcade.view()["street"] == "done"


def test_daily_cap_switches_to_local():
    env, arcade = make(daily_usd=0.0)
    arcade.open_holdem(ids(arcade, 2), sit=True, think=True)
    play_out(arcade)
    assert not [c for c in env.fake.calls if "打一桌德州扑克" in c.messages[0].content]
    assert "额度用完" in arcade.view()["note"]


def test_spectate_bet_settles():
    env, arcade = make(think_delay_s=0)
    members = ids(arcade, 3)
    arcade.open_holdem(members, sit=False, think=False)
    with pytest.raises(ArcadeError):
        arcade.act("check")  # 旁观不能出牌
    with pytest.raises(ArcadeError):
        arcade.deal(members[0], 99999)  # 超过一手上限
    arcade.deal(members[0], 100)
    assert arcade.wallet()["coins"] == 1900
    asyncio.run(arcade.drive())
    v = arcade.view()
    assert v["street"] == "done" and v["last_bet"]["on"] == members[0]
    coins = arcade.wallet()["coins"]
    if v["last_bet"]["win"]:
        assert coins == 1900 + v["last_bet"]["payout"]
    else:
        assert coins == 1900
    assert all(s["hole"][0] != "??" for s in v["seats"] if s["hole"])  # 旁观：结束后亮牌


def test_spectate_needs_two_members_and_bet_only_when_watching():
    env, arcade = make()
    with pytest.raises(ArcadeError):
        arcade.open_holdem(ids(arcade, 1), sit=False)
    arcade.open_holdem(ids(arcade, 2), sit=True, think=False)
    play_out(arcade)
    with pytest.raises(ArcadeError):
        arcade.deal(ids(arcade, 1)[0], 10)


def test_model_calls_are_recorded_on_hidden_chat_session():
    env, arcade = make()
    arcade.open_holdem(ids(arcade, 2), sit=True, think=True)
    play_out(arcade)
    start = NOW.replace(hour=0)
    end = NOW.replace(hour=23)
    n = env.rt.repo.count_in_steps("chat", (STEP,), start, end)
    assert n > 0


def test_option_lines_and_parse_pick():
    opts = [{"kind": "fold"}, {"kind": "call", "to": 40}, {"kind": "raise", "to": 80}]
    text = option_lines(opts, 40, 20)
    assert text.splitlines() == ["1. 弃牌", "2. 跟注（再放 20）", "3. 加注到 80"]
    assert parse_pick('{"pick": 2, "say": "来\\n吧"}', opts) == (opts[1], "来 吧")
    assert parse_pick('{"pick": "3"}', opts) == (opts[2], "")
    assert parse_pick('{"pick": 9}', opts) is None
    assert parse_pick("随便", opts) is None


def test_gifted_credit_does_not_count_toward_daily_cap(monkeypatch):
    env, arcade = make(daily_usd=0.0)
    monkeypatch.setattr(env.rt.router, "credit_left", lambda channel: True)
    arcade.open_holdem(ids(arcade, 2), sit=True, think=True)
    play_out(arcade)
    assert [c for c in env.fake.calls if "打一桌德州扑克" in c.messages[0].content]
    assert arcade.spent_today() == 0
