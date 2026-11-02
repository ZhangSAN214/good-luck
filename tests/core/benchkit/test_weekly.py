"""资料台周报：到点才出、关机开机补、先刷新过期资料、和上周比出新进榜、
不重复、每条有理由、存 Markdown。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone

from roundtable.core.benchkit.desk import Desk, Glossary
from roundtable.core.benchkit.weekly import Weekly, markdown, week_of

from .test_desk import CAPS, RULES, Net, gh

BJ = timezone(timedelta(hours=8))
MON_8 = datetime(2026, 11, 2, 8, 0, tzinfo=BJ)  # 周一 8 点（还没到 9 点）


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def setup(tmp_path, net, clock):
    desk = Desk(
        RULES,
        tmp_path,
        fetch_json=net.json,
        fetch_text=net.text,
        caps=CAPS,
        glossary=Glossary(terms={"engine": "游戏引擎"}),
        sleep=net.sleep,
        now=lambda: clock().astimezone(UTC),
    )
    return desk, Weekly(desk, now=clock)


def rising(name, stars, created="2026-10-25T00:00:00Z"):
    return gh(name, stars) | {"created_at": created}


def test_due_only_after_monday_nine_and_once_per_week(tmp_path):
    clock = Clock(MON_8)
    net = Net(rank={"topic%3A3d": [gh("a/one", 900)]})
    desk, wk = setup(tmp_path, net, clock)
    assert week_of(MON_8) == "2026-W45"
    assert not wk.due() and asyncio.run(wk.tick()) is None and not net.urls
    clock.t = MON_8 + timedelta(hours=1)
    assert wk.due()
    rep = asyncio.run(wk.tick())
    assert rep["week"] == "2026-W45" and net.urls  # 资料从没刷新过：先刷新
    assert not wk.due() and asyncio.run(wk.tick()) is None
    # 电脑周一关着：周三第一次开着时补上下一周的（这里是再下一周）
    clock.t = MON_8 + timedelta(days=9)
    assert wk.due() and asyncio.run(wk.tick())["week"] == "2026-W46"
    assert wk.weeks() == ["2026-W46", "2026-W45"]
    # 关掉就不出
    off = Weekly(
        Desk(
            RULES.model_copy(update={"weekly": False}),
            tmp_path,
            fetch_json=net.json,
            fetch_text=net.text,
            caps=CAPS,
        ),
        now=clock,
    )
    clock.t = MON_8 + timedelta(days=14, hours=2)
    assert not off.due() and not off.view()["enabled"]


def test_first_issue_lists_tops_then_new_entries_and_no_repeats(tmp_path):
    clock = Clock(MON_8 + timedelta(hours=2))
    net = Net(
        rank={
            "topic%3A3d%20created": [rising("r/fast", 3000)],
            "topic%3A3d": [gh("a/one", 90000), gh("b/two", 800)],
            "topic%3Agame-engine": [gh("g/engine", 700, desc="an engine")],
            "topic%3Alive2d": [gh("y/good", 500, "Apache-2.0")],
        }
    )
    desk, wk = setup(tmp_path, net, clock)
    rep = asyncio.run(wk.tick())
    by = {s["id"]: s for s in rep["sections"]}
    assert "第一期" in by["rank"]["title"]
    tops = by["rank"]["items"]
    assert [x["title"] for x in tops] == ["a/one", "g/engine"]
    assert tops[0]["why"] == "「3D」总榜第 1，9 万星"
    assert "游戏引擎" in tops[1]["zh"]  # 仓库条目带中文说明
    assert by["rising"]["items"][0]["title"] == "r/fast"
    assert "8 天前才建" in by["rising"]["items"][0]["why"]
    assert by["papers"]["items"][0]["why"].startswith("Hugging Face 每日论文点赞 9")
    assert any("live2d" in x["why"] for x in by["papers"]["items"])  # arXiv 按关注主题
    assert by["news"]["items"][0]["why"] == "HN 本周的新消息"
    assert "加进地图" in by["suggest"]["items"][0]["why"]
    assert all(x["why"] for s in rep["sections"] for x in s["items"])
    assert rep["intro"].startswith("这周有 ")
    md = (tmp_path / "_desk" / "weekly" / "2026-W45.md").read_text("utf-8-sig")
    assert md.startswith("# 工作台资料周报 2026-W45") and "[a/one](https://github.com/a/one)" in md

    # 下一周：总榜多了 c/three → 新进榜；上周写过的不再重复
    net.rank["topic%3A3d"] = [gh("c/three", 5000), gh("a/one", 90000)]
    clock.t += timedelta(days=7)
    rep2 = asyncio.run(wk.tick())
    by2 = {s["id"]: s for s in rep2["sections"]}
    assert by2["rank"]["title"] == "本周新进榜"
    assert [x["title"] for x in by2["rank"]["items"]] == ["c/three"]
    assert "本周新进「3D」总榜第 1 名" in by2["rank"]["items"][0]["why"]
    shown_last_week = {x["url"] for s in rep["sections"] for x in s["items"]}
    assert not shown_last_week & {x["url"] for s in rep2["sections"] for x in s["items"]}


def test_weekly_max_caps_items_and_markdown_strips_brackets(tmp_path):
    clock = Clock(MON_8 + timedelta(hours=2))
    net = Net(rank={"topic%3A3d": [gh("a/one", 900)]})
    desk, wk = setup(tmp_path, net, clock)
    wk.rules = RULES.model_copy(update={"weekly_max": 5})
    rep = asyncio.run(wk.tick(force=True))
    assert sum(len(s["items"]) for s in rep["sections"]) <= 5
    text = markdown(
        {
            "week": "2026-W45",
            "intro": "x",
            "generated_at": "2026-11-02T10:00:00+08:00",
            "sections": [
                {
                    "title": "t",
                    "items": [{"title": "a]\n(b", "url": "https://x", "why": "w", "zh": ""}],
                }
            ],
        }
    )
    assert "[a (b](https://x)" in text


def test_view_and_get_reject_bad_week_ids(tmp_path):
    clock = Clock(MON_8 + timedelta(hours=2))
    desk, wk = setup(tmp_path, Net(), clock)
    v = wk.view()
    assert v["enabled"] and v["when"] == "每周一 09:00" and v["latest"] is None and v["due"]
    assert wk.get("../../etc") is None and wk.get("2026-W01") is None
    asyncio.run(wk.tick())
    assert wk.view()["latest"]["week"] == "2026-W45"
    nodisk = Weekly(
        Desk(RULES, None, fetch_json=Net().json, fetch_text=Net().text, caps=CAPS), now=clock
    )
    assert not nodisk.due() and asyncio.run(nodisk.tick(force=True)) is None
