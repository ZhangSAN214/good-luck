"""资料台：GitHub 榜单（总榜 / 新星 / 新进榜）、能力地图核实、动态、建议卡、
给写窗口的助手挑工具。"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.benchkit.desk import (
    Capability,
    CapabilityMap,
    Desk,
    load_caps,
    parse_hf,
    repo_of,
)
from roundtable.core.config.loader import ConfigError
from roundtable.core.config.schema import BenchDesk

NOW = datetime(2026, 10, 31, 12, tzinfo=UTC)

RULES = BenchDesk(
    github_gap_s=7,
    topics=["live2d"],
    categories=[
        {"id": "3d", "label": "3D", "query": "topic:3d", "kinds": ["editor"]},
        {"id": "game", "label": "游戏", "query": "topic:game-engine", "kinds": ["game"]},
    ],
    feeds=[{"name": "HN", "url": "https://hn.example/rss"}],
)

CAPS = CapabilityMap(
    capabilities=[
        Capability(
            id="threejs",
            direction="3D",
            name="three.js",
            repo="mrdoob/three.js",
            use="3D 场景",
            license="MIT",
            difficulty="中",
            kinds=["editor"],
        ),
        Capability(
            id="phaser",
            direction="游戏",
            name="Phaser",
            repo="phaserjs/phaser",
            use="2D 游戏",
            license="MIT",
            difficulty="低",
            kinds=["game"],
        ),
    ]
)


def gh(name, stars, lic="MIT", desc="x"):
    return {
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "stargazers_count": stars,
        "description": desc,
        "license": {"spdx_id": lic},
        "pushed_at": "2026-10-30T00:00:00Z",
        "created_at": "2026-10-20T00:00:00Z",
        "topics": ["a"],
    }


RSS = (
    '<?xml version="1.0"?><rss><channel><item><title>Godot 5 发布</title>'
    "<link>https://hn.example/a</link><pubDate>Thu, 30 Oct 2026 10:00:00 GMT</pubDate>"
    "</item></channel></rss>"
)
ARXIV = (
    '<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Live2D motion</title>'
    "<id>http://arxiv.org/abs/2610.1</id><published>2026-10-29T00:00:00Z</published>"
    "<summary>s</summary></entry></feed>"
)


class Net:
    def __init__(self, rank=None, fail=()):
        self.urls: list[str] = []
        self.rank = rank or {}
        self.fail = fail
        self.slept: list[float] = []

    async def json(self, url):
        self.urls.append(url)
        if any(f in url for f in self.fail):
            raise RuntimeError("HTTP 403 token=secret-ish")
        if "daily_papers" in url:
            return [
                {
                    "paper": {"id": "2610.9", "title": "Splat", "upvotes": 9},
                    "publishedAt": "2026-10-31",
                }
            ]
        if "/repos/" in url:
            return gh(url.split("/repos/")[1], 12345, "MIT")
        for q, items in self.rank.items():
            if q in url:
                return {"items": items}
        return {"items": []}

    async def text(self, url):
        self.urls.append(url)
        return ARXIV if "arxiv" in url else RSS

    async def sleep(self, s):
        self.slept.append(s)


def make(tmp_path, net, now=NOW, caps=CAPS):
    return Desk(
        RULES,
        tmp_path,
        fetch_json=net.json,
        fetch_text=net.text,
        caps=caps,
        sleep=net.sleep,
        now=lambda: now,
    )


def test_repo_of_and_hf_parse_drop_junk():
    assert repo_of({"full_name": "x", "html_url": "https://github.com/x"}) is None
    assert repo_of(gh("a/b", 3, "NOASSERTION"))["license"] == ""
    got = parse_hf(
        [{"paper": {"id": "../evil", "title": "t"}}, {"paper": {"id": "1.2", "title": "ok"}}], 5
    )
    assert [x["url"] for x in got] == ["https://huggingface.co/papers/1.2"]


def test_refresh_ranks_each_category_marks_new_and_verifies_caps(tmp_path):
    net = Net(rank={"topic%3A3d": [gh("a/one", 900), gh("b/two", 800)]})
    desk = make(tmp_path, net)
    assert desk.due()
    asyncio.run(desk.refresh())
    v = desk.view()
    assert [r["repo"] for r in v["rank"]["3d"]["total"]] == ["a/one", "b/two"]
    assert not any(r["new"] for r in v["rank"]["3d"]["total"])  # 第一次没有「新进榜」
    # 新星只看最近 30 天新建的
    assert any("created%3A%3E2026-10-01" in u for u in net.urls)
    # 每次 GitHub 搜索之后都隔开（匿名每分钟 10 次）
    assert net.slept and set(net.slept) == {7}
    caps = {c["id"]: c for c in v["caps"]}
    assert caps["threejs"]["stars"] == 12345 and caps["threejs"]["verified"]
    kinds = {x["kind"] for x in v["feed"]}
    assert kinds == {"paper", "news"}
    assert not desk.due()

    # 第二次刷新：新进榜的标「新」
    net.rank = {"topic%3A3d": [gh("c/three", 1000), gh("a/one", 900)]}
    later = make(tmp_path, net, now=NOW + timedelta(days=2))
    asyncio.run(later.refresh())
    total = later.view()["rank"]["3d"]["total"]
    assert [(r["repo"], r["new"]) for r in total] == [("c/three", True), ("a/one", False)]


def test_failures_are_notes_not_crashes(tmp_path):
    net = Net(fail=("api.github.com", "daily_papers"))
    desk = make(tmp_path, net)
    d = asyncio.run(desk.refresh())
    assert d["notes"] and any("GitHub" in n for n in d["notes"])
    assert any("Hugging Face" in n for n in d["notes"])
    assert d["feed"]  # arXiv 和订阅照样有
    assert json.loads((tmp_path / "_desk" / "desk.json").read_text("utf-8"))["updated_at"]


def test_suggestions_need_stars_permissive_license_and_your_click(tmp_path):
    net = Net(
        rank={
            "topic%3Alive2d": [
                gh("mrdoob/three.js", 999),  # 地图里已经有
                gh("x/gpl", 500, "GPL-3.0"),  # 许可证不宽松
                gh("y/good", 500, "Apache-2.0"),
            ]
        }
    )
    desk = make(tmp_path, net)
    asyncio.run(desk.refresh())
    sug = desk.view()["suggest"]
    assert [s["repo"] for s in sug] == ["y/good"]
    assert any("stars%3A%3E%3D100" in u for u in net.urls)
    v = desk.decide(sug[0]["id"], True)
    assert v["suggest"] == [] and v["caps"][-1]["repo"] == "y/good" and v["caps"][-1]["added"]
    with pytest.raises(LookupError):
        desk.decide("nope", True)
    # 一周内不再找；过了一周再找也不会把已处理的再推一遍
    n = len(net.urls)
    asyncio.run(make(tmp_path, net, now=NOW + timedelta(days=1)).refresh())
    assert not any("stars%3A" in u for u in net.urls[n:])
    asyncio.run(make(tmp_path, net, now=NOW + timedelta(days=8)).refresh())
    assert make(tmp_path, net).view()["suggest"] == []


def test_text_for_picks_tools_by_kind(tmp_path):
    net = Net(rank={"topic%3Agame-engine": [gh("g/engine", 700, desc="engine")]})
    desk = make(tmp_path, net)
    asyncio.run(desk.refresh())
    game = desk.text_for("game")
    assert game.splitlines()[0].startswith("- Phaser")
    assert "three.js" not in game and "榜单·游戏：g/engine" in game
    assert "three.js" in desk.text_for("editor")


def test_start_refresh_runs_once_in_background(tmp_path):
    net = Net()
    desk = make(tmp_path, net)

    async def go():
        assert desk.start_refresh() is True
        assert desk.busy and desk.start_refresh() is False
        await desk.wait()
        return desk.busy

    assert asyncio.run(go()) is False
    off = Desk(
        RULES.model_copy(update={"enabled": False}),
        tmp_path,
        fetch_json=net.json,
        fetch_text=net.text,
        caps=CAPS,
    )

    async def go_off():
        return off.start_refresh()

    assert asyncio.run(go_off()) is False


def test_repo_capability_map_is_valid_and_covers_every_kind(tmp_path):
    from roundtable.core.config import load_config

    caps = load_caps()
    assert len(caps.capabilities) >= 20
    bench = load_config().roundtable.bench
    kinds = set(bench.skills.kinds)
    for c in caps.capabilities:
        assert set(c.kinds) <= kinds, (c.id, c.kinds)  # 只用配置里有的工作台类型
    for cat in bench.desk.categories:
        assert set(cat.kinds) <= kinds, (cat.id, cat.kinds)
    for k in ("editor", "motion3d", "game", "sim", "dashboard", "audio"):
        assert any(k in c.kinds for c in caps.capabilities)
    bad = tmp_path / "bad.yaml"
    bad.write_text("capabilities:\n  - {id: a, direction: x, name: a, use: u, extra: 1}\n", "utf-8")
    with pytest.raises(ConfigError, match="bad.yaml"):
        load_caps(bad)
    dup = tmp_path / "dup.yaml"
    dup.write_text(
        "capabilities:\n"
        "  - {id: a, direction: x, name: a, use: u}\n"
        "  - {id: a, direction: x, name: b, use: u}\n",
        "utf-8",
    )
    with pytest.raises(ConfigError, match="重复"):
        load_caps(dup)
    assert load_caps(tmp_path / "none.yaml").capabilities == []


def test_unreadable_capability_says_unprobed_with_reason(tmp_path):
    """只读面板读不到就写「不可探测 + 原因」，不装作有数；上次核实的数留着。"""
    caps = CapabilityMap(
        capabilities=[
            *CAPS.capabilities,
            Capability(id="local", direction="x", name="本地", use="u"),
        ]
    )
    net = Net()
    desk = make(tmp_path, net, caps=caps)
    asyncio.run(desk.refresh())
    v = {c["id"]: c for c in desk.view()["caps"]}
    assert v["threejs"]["stars"] == 12345 and "unprobed" not in v["threejs"]
    assert "没有 GitHub 仓库" in v["local"]["unprobed"]
    net.fail = ("/repos/",)
    later = make(tmp_path, net, now=NOW + timedelta(days=2), caps=caps)
    asyncio.run(later.refresh())
    v = {c["id"]: c for c in later.view()["caps"]}
    assert "这次没读到" in v["threejs"]["unprobed"] and v["threejs"]["stars"] == 12345
    assert "限速" in v["threejs"]["unprobed"] and "token" not in v["threejs"]["unprobed"]
