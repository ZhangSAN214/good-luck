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


# --- 斗地主 / 跑得快 -----------------------------------------------------------


def play_cards(arcade: Arcade) -> None:
    """你每次挑第一项（叫分 / 出牌 / 不要），成员按各自的方式出，直到这一局结束。"""
    for _ in range(300):
        asyncio.run(arcade.drive())
        v = arcade.view()
        if v["phase"] == "done":
            return
        o = v["options"][0]
        arcade.play(o)
    raise AssertionError("这一局没打完")


@pytest.mark.parametrize("game", ["doudizhu", "paodekuai"])
def test_cards_sit_play_and_coins_follow_points(game):
    env, arcade = make()
    with pytest.raises(ArcadeError):
        arcade.open_cards(game, ids(arcade, 3), sit=True)  # 你加两位正好三人
    v = arcade.open_cards(game, ids(arcade, 2), sit=True, think=True)
    assert v["game"] == game and len(v["seats"]) == 3 and v["seated"]
    play_cards(arcade)
    v = arcade.view()
    me = next(r for r in v["results"] if r["seat"] == YOU)
    assert v["last_coins"] == me["points"] * 10
    assert arcade.wallet()["coins"] == 2000 + v["last_coins"]
    assert v["can_deal"]
    calls = [c for c in env.fake.calls if "正在和朋友们打" in c.messages[0].content]
    assert calls and len({json.dumps(c.params, sort_keys=True) for c in calls}) == 1
    arcade.deal()
    assert arcade.view()["phase"] in ("bid", "play")


def test_cards_spectate_bet():
    env, arcade = make(think_delay_s=0)
    members = ids(arcade, 3)
    with pytest.raises(ArcadeError):
        arcade.open_cards("doudizhu", members[:2], sit=False)
    arcade.open_cards("doudizhu", members, sit=False, think=False)
    with pytest.raises(ArcadeError):
        arcade.play({"kind": "pass"})
    arcade.deal(members[1], 100)
    asyncio.run(arcade.drive())
    v = arcade.view()
    assert v["phase"] == "done" and v["last_bet"]["on"] == members[1]
    expect = 1900 + (v["last_bet"]["payout"] if v["last_bet"]["win"] else 0)
    assert arcade.wallet()["coins"] == expect
    assert all(s["cards"] or s["count"] == 0 for s in v["seats"])  # 结束后亮牌


def test_cards_loss_capped_at_balance():
    env, arcade = make()
    arcade._add_coins(-1995)
    arcade.open_cards("paodekuai", ids(arcade, 2), sit=True, think=False)
    play_cards(arcade)
    assert arcade.wallet()["coins"] >= 0


def test_shengji_sit_with_three_members_and_coins_per_level():
    env, arcade = make()
    with pytest.raises(ArcadeError):
        arcade.open_cards("shengji", ids(arcade, 2), sit=True)  # 你加三位正好四人
    v = arcade.open_cards("shengji", ids(arcade, 3), sit=True, think=True)
    assert v["game"] == "shengji" and len(v["seats"]) == 4 and v["phase"] == "bid"
    play_cards(arcade)
    v = arcade.view()
    me = next(r for r in v["results"] if r["seat"] == YOU)
    assert v["last_coins"] == me["points"] * 50
    assert v["summary"]["attack_points"] >= 0 and v["levels"]
    calls = [c for c in env.fake.calls if "正在和朋友们打升级" in c.messages[0].content]
    assert calls  # 成员用模型想过
    arcade.deal()
    assert arcade.view()["phase"] == "bid"


def test_shengji_spectate_bet_pays_double_for_a_team():
    env, arcade = make(think_delay_s=0)
    members = ids(arcade, 4)
    arcade.open_cards("shengji", members, sit=False, think=False)
    arcade.deal(members[0], 100)
    asyncio.run(arcade.drive())
    v = arcade.view()
    assert v["phase"] == "done"
    if v["last_bet"]["win"]:
        assert v["last_bet"]["payout"] == 200  # 四人里两人赢：押中赔两倍


def test_god_view_only_when_watching():
    env, arcade = make(think_delay_s=0)
    members = ids(arcade, 4)
    arcade.open_cards("shengji", members, sit=False, think=False)
    arcade.deal()
    asyncio.run(arcade.drive())
    arcade.set_god(False)
    arcade.deal()
    v = arcade.view()
    assert all(s["cards"] == [] for s in v["seats"]) or v["phase"] == "done"
    v = arcade.set_god(True)
    assert v["god"] and all(s["cards"] or s["count"] == 0 for s in v["seats"])
    arcade.leave()
    arcade.open_cards("doudizhu", ids(arcade, 2), sit=True, think=False)
    with pytest.raises(ArcadeError):
        arcade.set_god(True)


def test_members_get_tracker_notes_and_holdem_odds():
    env, arcade = make()
    arcade.open_cards("doudizhu", ids(arcade, 2), sit=True, think=True)
    play_cards(arcade)
    users = [
        c.messages[-1].content
        for c in env.fake.calls
        if "正在和朋友们打斗地主" in c.messages[0].content
    ]
    assert any("别人手里还有" in u for u in users)  # 记牌器
    assert any("出完还剩" in u or "能一手出完" in u for u in users)  # 出法后果标注
    arcade.leave()
    arcade.open_holdem(ids(arcade, 2), sit=True, think=True)
    play_out(arcade)
    users = [
        c.messages[-1].content for c in env.fake.calls if "打一桌德州扑克" in c.messages[0].content
    ]
    assert users and all("赢的机会约" in u for u in users)


def test_mahjong_sit_with_three_members_and_spectate():
    env, arcade = make()
    with pytest.raises(ArcadeError):
        arcade.open_cards("mahjong", ids(arcade, 2), sit=True)  # 你加三位正好四人
    v = arcade.open_cards("mahjong", ids(arcade, 3), sit=True, think=True)
    assert v["game"] == "mahjong" and len(v["seats"]) == 4 and v["wall"] == 136 - 53
    play_cards(arcade)
    v = arcade.view()
    me = next(r for r in v["results"] if r["seat"] == YOU)
    assert v["last_coins"] == me["points"] * 10
    calls = [c for c in env.fake.calls if "正在和朋友们打麻将" in c.messages[0].content]
    assert calls  # 成员用模型想过，提示词里有记牌器
    assert any("牌墙还剩" in c.messages[-1].content for c in calls)
    arcade.leave()
    members = ids(arcade, 4)
    arcade.open_cards("mahjong", members, sit=False, think=False)
    arcade.deal(members[0], 100)
    asyncio.run(arcade.drive())
    v = arcade.view()
    assert v["phase"] == "done"
    lb = v["last_bet"]
    assert lb["payout"] == (400 if lb["win"] else 100 if lb.get("draw") else 0)


def test_tanks_start_result_coins_once_and_daily_cap():
    from datetime import timedelta

    env, arcade = make()
    with pytest.raises(ArcadeError):
        arcade.tanks_start(ids(arcade, 4))  # 最多 3 位
    run = arcade.tanks_start(ids(arcade, 3))
    assert run["seed"] >= 0 and len(run["members"]) == 3 and run["lives"] == 3
    assert arcade.status()["tanks"]["today"] == 0
    # 打得太短不给
    r = arcade.tanks_result(run["token"], 1, 3)
    assert r["coins"] == 0 and "秒" in r["note"]
    with pytest.raises(ArcadeError):
        arcade.tanks_result(run["token"], 1, 3)  # 同一局只算一次
    later = NOW + timedelta(minutes=2)
    run = arcade.tanks_start(ids(arcade, 2))
    env.rt.budget.now = lambda: later
    r = arcade.tanks_result(run["token"], 1, 2)
    assert r["coins"] == 100 + 2 * 20 and arcade.wallet()["coins"] == 2000 + 140
    with pytest.raises(ArcadeError):
        arcade.tanks_result("别的凭证", 1, 0)
    # 每天上限 600
    for _ in range(5):
        env.rt.budget.now = lambda: NOW
        run = arcade.tanks_start(ids(arcade, 1))
        env.rt.budget.now = lambda: later
        r = arcade.tanks_result(run["token"], 1, 5)
    assert arcade.status()["tanks"]["today"] == 600
    assert r["coins"] == 0 and "上限" in r["note"]
    assert arcade.wallet()["coins"] == 2000 + 600


def test_tanks_team_watch_stats_and_lines():
    from datetime import timedelta

    env, arcade = make()
    four = ids(arcade, 4)
    with pytest.raises(ArcadeError, match="3 位"):
        arcade.tanks_start(four[:2], mode="team")  # 2 对 2 上场要正好 3 位
    with pytest.raises(ArcadeError, match="4 位"):
        arcade.tanks_start(four[:3], mode="team", watch=True)
    with pytest.raises(ArcadeError, match="玩法"):
        arcade.tanks_start(four[:1], mode="闯关")
    run = arcade.tanks_start(four[:3], mode="team")
    assert run["mode"] == "team" and all("lines" in m for m in run["members"])
    later = NOW + timedelta(minutes=2)
    env.rt.budget.now = lambda: later
    rank = [{"id": four[0], "place": 1, "kills": 2}, {"id": four[1], "place": 2, "kills": 1}]
    r = arcade.tanks_result(run["token"], 1, 1, rank + [{"id": "外人", "place": 1, "kills": 9}])
    assert r["coins"] == 100 + 20  # 你们队赢
    # 旁观：不给金币，只记战绩
    env.rt.budget.now = lambda: NOW
    run = arcade.tanks_start(four, watch=True)
    env.rt.budget.now = lambda: later
    r = arcade.tanks_result(run["token"], 1, 5, [{"id": four[0], "place": 1, "kills": 3}])
    assert r["coins"] == 0
    stats = {s["id"]: s for s in arcade.status()["tanks"]["stats"]}
    assert stats[four[0]] == {**stats[four[0]], "games": 2, "wins": 2, "kills": 5}
    assert stats["you"]["games"] == 1 and stats["you"]["wins"] == 1
    assert stats[four[1]]["wins"] == 0 and "外人" not in stats
    assert arcade.status()["tanks"]["stats"][0]["id"] == four[0]  # 赢得多的排前面
