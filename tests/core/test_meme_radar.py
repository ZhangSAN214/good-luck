"""梗雷达：热榜解析（JSON 路径 / RSS）、一天一次、续保质期、提炼后程序再筛（老梗、你删过的、
太早火的、拿不准意思的不收）、经典梗补意思、搜索次数上限、花费闸门、关掉不动。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.browse import FeedError
from roundtable.core.config.schema import ClassicMeme, MemeSource
from roundtable.core.member.memes import MemeBook
from roundtable.core.meme_radar import SEARCH_STEP, STEP, MemeRadar, parse_hot
from roundtable.core.search import SearchService
from roundtable.core.search.fake import FakeSearch
from roundtable.core.storage.repository import iso

from .orchestrator.conftest import Env

NOW = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)
WEIBO = json.dumps(
    {"data": {"realtime": [{"word": "康神开播了"}, {"word": "某地暴雨"}, {"note": "无标题"}]}}
).encode()
ZHIHU = json.dumps({"data": [{"target": {"title": "如何评价虾爹的新赛季"}}]}).encode()
SOURCES = [
    MemeSource(name="微博", url="https://hot.test/weibo", path="data.realtime", key="word"),
    MemeSource(name="知乎", url="https://hot.test/zhihu", path="data", key="target.title"),
    MemeSource(name="坏的", url="https://bad.test/x", path="data", key="word"),
]
DISTILLED = {
    "memes": [
        {
            "phrase": "康神开播",
            "meaning": "康神终于开直播了",
            "cue": "康神开播了",
            "reply": "真的假的？！",
            "example": "康神开播了 / 真的假的？！",
            "since": "2026-10",
        },
        {"phrase": "绝绝子", "meaning": "很好", "since": "2026-10"},  # 老梗：程序拦下
        {"phrase": "上古梗", "meaning": "很早以前的", "since": "2019-05"},  # 太早火的
        {"phrase": "瞎猜的梗", "meaning": ""},  # 没写意思
        {"phrase": "删过的梗", "meaning": "你删过", "since": "2026-10"},
    ],
    "seen": ["旧梗"],
    "explained": [{"phrase": "虾爹", "meaning": "NiKo 的外号", "cue": "NiKo", "reply": "虾爹来了"}],
}


def env_with(**update) -> Env:
    env = Env()
    env.rt.budget.now = lambda: NOW
    cfg = env.rt.config.roundtable
    rules = cfg.chat.memes.model_copy(
        update={
            "sources": SOURCES,
            "classics": [ClassicMeme(phrase="虾爹", aliases=["NiKo"])],
            "stale": ["绝绝子"],
            **update,
        }
    )
    chat = cfg.chat.model_copy(update={"memes": rules})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )
    return env


def radar(env: Env, allow=lambda usd: True, log=None) -> MemeRadar:
    async def fetch(url, timeout_s, max_bytes):
        if log is not None:
            log.append(url)
        if "bad" in url:
            raise RuntimeError("boom")
        return WEIBO if "weibo" in url else ZHIHU

    return MemeRadar(env.rt, allow, fetch=fetch)


def distill_calls(env: Env) -> list:
    return [c for c in env.fake.calls if "最近网上在玩什么梗" in c.messages[0].content]


def answer(env: Env, data: dict) -> None:
    old = env.fake._default

    def reply(model, messages):
        if "最近网上在玩什么梗" in messages[0].content:
            return json.dumps(data, ensure_ascii=False)
        return old(model, messages)

    env.fake._default = reply


def test_parse_hot_follows_paths_and_keys():
    assert parse_hot(WEIBO, SOURCES[0]) == ["康神开播了", "某地暴雨"]
    assert parse_hot(ZHIHU, SOURCES[1]) == ["如何评价虾爹的新赛季"]
    deep = MemeSource(name="d", url="https://d.test", path="data.cards.0.content")
    doc = json.dumps({"data": {"cards": [{"content": [{"word": "甲"}, {"title": "乙"}]}]}})
    assert parse_hot(doc.encode(), deep) == ["甲", "乙"]  # 没写 key：常见字段名
    star = MemeSource(name="s", url="https://s.test", path="list.*.items", key="t")
    doc = json.dumps({"list": [{"items": [{"t": "一"}]}, {"items": [{"t": "二"}]}]})
    assert parse_hot(doc.encode(), star) == ["一", "二"]
    with pytest.raises(FeedError):
        parse_hot(b"not json", SOURCES[0])
    with pytest.raises(FeedError):
        parse_hot(b'{"data": {}}', SOURCES[0])
    rss = MemeSource(name="r", url="https://r.test", kind="rss")
    feed = (
        b"<rss><channel><item><title>A</title><link>https://r.test/a</link></item></channel></rss>"
    )
    assert parse_hot(feed, rss) == ["A"]


async def test_refresh_collects_fresh_memes_and_filters_the_rest():
    env = env_with()
    book = MemeBook(env.rt)
    book.add({"phrase": "删过的梗", "meaning": "x", "classic": False}, NOW)
    book.remove("删过的梗")
    book.add({"phrase": "旧梗", "meaning": "前阵子的", "classic": False}, NOW - timedelta(days=10))
    answer(env, DISTILLED)
    log: list[str] = []
    out = await radar(env, log=log).refresh(NOW)
    assert out["titles"] == 3 and out["failed"] == 1 and out["added"] == 1
    assert out["dropped"] == 3 and out["explained"] == 1
    memes = {m["phrase"]: m for m in book.all(NOW)}
    assert set(memes) == {"虾爹", "康神开播", "旧梗"}
    assert memes["康神开播"]["reply"] == "真的假的？！" and not memes["康神开播"]["classic"]
    assert memes["虾爹"]["meaning"] == "NiKo 的外号" and memes["虾爹"]["classic"]
    assert memes["旧梗"]["last_seen"] == iso(NOW)  # 又上榜：续保质期
    (call,) = distill_calls(env)
    user = call.messages[-1].content
    assert "<hotlist>" in user and "康神开播了" in user and "绝绝子" in user  # 老梗名单交给它
    rows = env.rt.repo.conn.execute("SELECT step FROM calls").fetchall()
    assert STEP in {r[0] for r in rows}
    # 一天一次：马上再推进不重复抓、不再调用
    again = await radar(env, log=log).refresh(NOW + timedelta(hours=2))
    assert again["titles"] == 0 and len(distill_calls(env)) == 1
    forced = await radar(env, log=log).refresh(NOW + timedelta(hours=2), force=True)
    assert forced["titles"] == 3 and len(distill_calls(env)) == 2


async def test_memes_off_the_charts_for_two_weeks_are_dropped_and_the_cap_holds():
    env = env_with(max_memes=5)
    book = MemeBook(env.rt)
    book.add({"phrase": "过气梗", "meaning": "x", "classic": False}, NOW - timedelta(days=20))
    many = {"memes": [{"phrase": f"新梗{i}", "meaning": "y", "since": "2026-10"} for i in range(8)]}
    answer(env, many)
    await radar(env).refresh(NOW)
    stored = [m["phrase"] for m in book.stored()]
    assert "过气梗" not in stored
    assert len([p for p in stored if p.startswith("新梗")]) == 5


async def test_search_supplements_up_to_the_daily_limit_and_explains_a_classic():
    env = env_with(search_per_day=2)
    fake = FakeSearch()
    env.rt.search = SearchService({"fake-search": fake})
    answer(env, {"memes": []})
    await radar(env).refresh(NOW)
    assert len(fake.queries) == 2
    assert "虾爹" in fake.queries[1]  # 第二次查还不知道意思的经典梗
    rows = env.rt.repo.conn.execute("SELECT step FROM calls WHERE step = ?", (SEARCH_STEP,))
    assert len(rows.fetchall()) == 2
    await radar(env).refresh(NOW + timedelta(hours=1), force=True)
    assert len(fake.queries) == 2  # 今天的次数用完了


async def test_no_model_call_when_the_life_cap_says_no_and_nothing_when_switched_off():
    env = env_with()
    answer(env, DISTILLED)
    out = await radar(env, allow=lambda usd: False).refresh(NOW)
    assert out["titles"] == 3 and not distill_calls(env)
    env2 = env_with(enabled=False)
    log: list[str] = []
    assert (await radar(env2, log=log).refresh(NOW))["titles"] == 0 and not log
