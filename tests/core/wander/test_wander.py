"""成员自己上网：浏览器工具函数、决定解析、一次出门的推进、心跳自动安排。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from roundtable.core.chat import ChatHub
from roundtable.core.config.schema import WanderSite
from roundtable.core.wander import (
    FakeWeb,
    Snapshot,
    Wanderer,
    host_allowed,
    keep_links,
    parse_decision,
    tidy,
    wander_room,
)

from ..orchestrator.conftest import Env

HOME = "https://www.bilibili.com/"
VIDEO = "https://www.bilibili.com/video/BV1"
SEARCH = "https://search.bilibili.com/all?keyword=%E7%8C%AB"
SITE = WanderSite(
    id="bilibili",
    name="B站",
    home=HOME,
    search="https://search.bilibili.com/all?keyword={q}",
    hosts=["bilibili.com"],
)


def pages() -> dict[str, Snapshot]:
    return {
        HOME: Snapshot(HOME, "哔哩哔哩首页", "推荐：猫咪合集\n游戏实况", [("猫咪合集", VIDEO)]),
        VIDEO: Snapshot(VIDEO, "猫咪合集", "一只橘猫在睡觉。播放 10 万", [("回首页", HOME)]),
        SEARCH: Snapshot(SEARCH, "搜索：猫", "猫的视频若干", []),
    }


def step(action, note="", **kw) -> str:
    return json.dumps({"note": note, "action": action, **kw}, ensure_ascii=False)


def wander_env(web=None, **rules):
    env = Env()
    chat = env.config.roundtable.chat
    update = {"sites": [SITE], "step_wait_seconds": 0} | rules
    w = chat.wander.model_copy(update=update)
    rt = env.config.roundtable.model_copy(update={"chat": chat.model_copy(update={"wander": w})})
    env.rt.config = env.config.model_copy(update={"roundtable": rt})
    fake = web or FakeWeb(pages())
    env.rt.wander_web = lambda: fake
    events: list = []
    allowed = {"ok": True}
    hub = ChatHub(env.rt)
    wanderer = Wanderer(
        env.rt, hub, lambda usd: allowed["ok"], profiles=Path("/nowhere"), publish=events.append
    )
    return env, wanderer, fake, events, allowed


# ---------- 工具函数 ----------


def test_host_allowed_is_suffix_match_on_http_only():
    assert host_allowed("https://www.bilibili.com/x", ["bilibili.com"])
    assert host_allowed("https://bilibili.com", ["bilibili.com"])
    assert not host_allowed("https://evilbilibili.com/", ["bilibili.com"])
    assert not host_allowed("javascript:alert(1)", ["bilibili.com"])
    assert not host_allowed("https://taobao.com/", ["bilibili.com"])


def test_keep_links_filters_dedupes_and_limits():
    raw = [
        ["猫咪合集", VIDEO + "#top"],
        ["猫咪合集", VIDEO],
        ["", HOME],
        ["外站", "https://example.com/"],
        ["x", HOME],
        ["游戏", "https://www.bilibili.com/game"],
        "bad",
    ]
    assert keep_links(raw, ["bilibili.com"], 10) == [
        ("猫咪合集", VIDEO),
        ("游戏", "https://www.bilibili.com/game"),
    ]
    assert len(keep_links(raw, ["bilibili.com"], 1)) == 1


def test_tidy_collapses_blank_lines_and_cuts():
    assert tidy("  a \n\n\t b　 c \n", 100) == "a\nb c"
    assert tidy("x" * 50, 10) == "x" * 10


def test_parse_decision_validates():
    assert parse_decision(step("open", "看看", link=1), 1).link == 1
    assert parse_decision(step("open", link=2), 1) is None  # 超出范围
    assert parse_decision(step("search", query="猫"), 0).query == "猫"
    assert parse_decision(step("search"), 0) is None
    assert parse_decision(step("click"), 3) is None  # 不支持的动作
    assert parse_decision("随便说说", 3) is None
    d = parse_decision("```json\n" + step("done", summary="逛完了") + "\n```", 0)
    assert d.action == "done" and d.summary == "逛完了"


# ---------- 一次出门 ----------


async def test_trip_reads_pages_follows_links_and_records():
    env, w, web, events, _ = wander_env()
    env.fake.queue("b1", step("open", "首页有猫咪合集，点进去看看", link=1))
    env.fake.queue("b1", step("search", "这只橘猫好懒", query="猫"))
    env.fake.queue("b1", step("done", "", summary="看了猫咪合集，又搜了猫，挺治愈的"))
    w.start("V1", "bilibili")
    running = w.status()["running"]
    assert running["member"] == "V1" and running["site"] == "B站" and running["by"] == "user"
    await w.wait()
    assert web.visited == [HOME, VIDEO, SEARCH] and web.closed
    assert web.profile == Path("/nowhere/V1") and web.hosts == ["bilibili.com"]
    log = w.log("V1")
    kinds = [m["meta"]["wander"]["kind"] for m in log]
    assert kinds == ["start", "page", "page", "summary", "end"]
    page = log[1]
    assert page["author"] == "V1" and page["meta"]["wander"]["url"] == HOME
    assert log[2]["meta"]["wander"]["title"] == "猫咪合集"
    assert "看了 3 页" in log[-1]["text"]
    # 每页一次调用：页面文字和链接在标签里，用 V1 自己的模型
    calls = [c for c in env.fake.calls if c.model == "b1"]
    assert len(calls) == 3
    first = calls[0].messages[-1].content
    assert "<page>" in first and "猫咪合集" in first and "1. 猫咪合集" in first
    assert "用这个网站的搜索功能" in calls[0].messages[0].content
    # 小结发成感知事件；状态回到空闲
    assert [e.kind for e in events] == ["perceive.web"] and events[0].member == "V1"
    assert w.status()["running"] is None and w.trips_today("V1") == 1
    # 隐藏会话：不在群聊列表里；花费记在 wander 步骤上
    hub = ChatHub(env.rt)
    assert all(r["id"] != wander_room("V1") for r in hub.rooms())
    with pytest.raises(KeyError):
        hub.room(wander_room("V1"))
    steps = {r["step"] for r in env.rt.repo.conn.execute("SELECT step FROM calls").fetchall()}
    assert "wander" in steps


async def test_only_one_trip_at_a_time_and_checks():
    env, w, *_ = wander_env()
    env.fake.queue("b1", step("done", summary="随便看看"))
    with pytest.raises(ValueError, match="不在"):
        w.start("nobody")
    with pytest.raises(ValueError, match="白名单"):
        w.start("V1", "taobao")
    w.start("V1")
    with pytest.raises(ValueError, match="一次只能一位"):
        w.start("V2")
    await w.wait()


async def test_budget_stops_trip_and_blocks_start():
    env, w, web, _, allowed = wander_env()
    allowed["ok"] = False
    with pytest.raises(ValueError, match="上限"):
        w.start("V1")
    allowed["ok"] = True
    env.fake.queue("b1", step("open", "看看", link=1))
    w.start("V1")
    allowed["ok"] = False  # 看完第一页后额度用完
    await w.wait()
    assert "花费到上限" in w.log("V1")[-1]["text"]


async def test_stop_and_max_steps():
    env, w, web, *_ = wander_env(max_steps=2)
    env.fake.queue("b1", step("open", "看看", link=1))
    env.fake.queue("b1", step("back", "回去吧"))
    w.start("V1")
    await w.wait()
    assert web.visited == [HOME, VIDEO] and "看了 2 页" in w.log("V1")[-1]["text"]
    env, w, web, *_ = wander_env()
    env.fake.queue("b1", step("open", "看看", link=1))
    w.start("V1")
    w.stop()
    await w.wait()
    assert "你叫停了" in w.log("V1")[-1]["text"]


async def test_bad_replies_and_broken_pages_end_gracefully():
    env, w, *_ = wander_env()
    env.fake.queue("b1", "我想想")
    env.fake.queue("b1", step("click"))
    w.start("V1")
    await w.wait()
    assert "没想好" in w.log("V1")[-1]["text"]
    broken = FakeWeb({HOME: Snapshot(HOME, "首页", "x", [("坏链接", HOME + "404")])})
    env, w, *_ = wander_env(web=broken)
    env.fake.queue("b1", step("open", link=1))
    env.fake.queue("b1", step("open", link=1))
    w.start("V1")
    await w.wait()
    texts = [m["text"] for m in w.log("V1")]
    assert any("404" in t for t in texts) and "一直打不开" in texts[-1]
    env, w, *_ = wander_env(web=FakeWeb({}, fail_start="找不到能用的浏览器"))
    w.start("V1")
    await w.wait()
    assert "找不到能用的浏览器" in w.log("V1")[-1]["text"]
    assert w.status()["last_error"]


async def test_same_prompt_and_params_for_everyone():
    def first_call(member):
        env, w, *_ = wander_env()
        model = {"V1": "b1", "V2": "b2"}[member]
        env.fake.queue(model, step("done", summary="好"))
        return env, w, model

    out = []
    for member in ("V1", "V2"):
        env, w, model = first_call(member)
        w.start(member)
        await w.wait()
        call = next(c for c in env.fake.calls if c.model == model)
        out.append((call.messages[0].content, call.params))
    system_a, params_a = out[0]
    system_b, params_b = out[1]
    assert params_a == params_b
    names = [m.name for m in ChatHub(env.rt).everyone() if m.id in ("V1", "V2")]
    assert system_a.replace(names[0], "X") == system_b.replace(names[1], "X")


# ---------- 心跳 ----------


def test_want_only_when_idle_awake_and_not_done_today(monkeypatch):
    env, w, *_ = wander_env(trip_chance=1.0)
    from roundtable.core.wander import wander as mod

    monkeypatch.setattr(mod, "schedule", lambda m, h, d: ("lounge", "game"))
    evening = datetime(2026, 10, 29, 20, 0).astimezone()
    intent = w.want("V1", evening)
    assert intent is not None and intent.member == "V1" and intent.estimate_usd > 0
    assert w.want("V1", evening.replace(hour=8)) is None  # 太早
    monkeypatch.setattr(mod, "schedule", lambda m, h, d: ("office", "work"))
    assert w.want("V1", evening) is None  # 上班时不逛
    monkeypatch.setattr(mod, "schedule", lambda m, h, d: ("lounge", "game"))
    env2, w2, *_ = wander_env(trip_chance=1.0, auto=False)
    assert w2.want("V1", evening) is None


async def test_heartbeat_intent_starts_trip(monkeypatch):
    env, w, web, *_ = wander_env(trip_chance=1.0)
    from roundtable.core.wander import wander as mod

    monkeypatch.setattr(mod, "schedule", lambda m, h, d: ("lounge", "game"))
    env.fake.queue("b1", step("done", summary="逛了一圈"))
    intent = w.want("V1", datetime(2026, 10, 29, 20, 0).astimezone())
    await intent.run()
    assert w.trip is not None and w.trip.by == "auto"
    await w.wait()
    assert "空闲时" in w.log("V1")[0]["text"]
    assert w.want("V1", datetime(2026, 10, 29, 21, 0).astimezone()) is None  # 今天逛过了
