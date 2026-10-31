"""三位助手协作：方案会（各提方案、互投不投自己、主案定做法）、对抗手（严重问题挡过线）、
协作看板、dsh 插件学习卡和装插件申请。超级电脑、质检都是假的。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.benchkit.builder import BenchBuilder
from roundtable.core.config.schema import BenchRules
from roundtable.core.supercomputer.runner import RunResult

from .test_builder import FakeResearcher, FakeSC, LearnScout, ResearchSC, W, fake_check
from .test_builder import render as render_json


class TeamSC(FakeSC):
    """方案会：每人一份方案；投票按 votes 表（投票人 → 编号作者）；对抗手按 bugs 交问题。"""

    def __init__(self, *a, votes=None, bugs=None, broken=(), **kw):
        super().__init__(*a, **kw)
        self.votes = votes or {}
        self.bugs = bugs if bugs is not None else (lambda n: [])
        self.attack_n = 0
        self.broken = set(broken)  # 这些人提的方案看不懂

    async def step(self, kind, prompt, **kw):
        p = json.loads(prompt)
        role = p["role"]
        if role not in ("bench_pitch", "bench_vote", "bench_attack"):
            return await super().step(kind, prompt, **kw)
        exclude, preset = kw.get("exclude", ()), kw.get("preset")
        pool = [w for w in self.workers if w not in exclude]
        who = (preset if preset in pool else pool[0]) if pool else None
        self.calls.append({"role": role, "who": who, "exclude": exclude, "preset": preset})
        if who is None:  # 没人可派：一次也没调用
            return None, None
        kw["record"]["worker"] = who
        self.prompts.append(prompt)
        if role == "bench_pitch":
            if who in self.broken:
                return W(who), RunResult(0, "我觉得挺好")
            out = {
                "idea": f"{who} 的思路",
                "hero": f"{who} 的主视觉",
                "plan": [f"{who} 做法一", f"{who} 做法二"],
                "risks": [f"{who} 风险"],
                "cut": [],
            }
            return W(who), RunResult(0, json.dumps(out, ensure_ascii=False))
        if role == "bench_vote":
            # 从方案文字里认出「谁的方案是几号」，按 votes 表投
            label = {}
            for block in p["pitches"].split("\n\n"):
                head = block.split("：", 1)
                label[head[1].split(" ")[0]] = head[0].removeprefix("方案 ")
            want = self.votes.get(who)
            pick = label.get(want, "Z") if want else next(iter(label.values()))
            out = {"pick": pick, "why": "能落地", "borrow": [f"{who} 建议补撤销"]}
            return W(who), RunResult(0, json.dumps(out, ensure_ascii=False))
        self.attack_n += 1
        out = {"bugs": self.bugs(self.attack_n), "summary": "找了一圈"}
        return W(who), RunResult(0, json.dumps(out, ensure_ascii=False))


def make(tmp_path, sc, team=None, **rules):
    return BenchBuilder(
        BenchRules(team={"enabled": True, **(team or {})}, **rules),
        root=tmp_path,
        supercomputer=sc,
        render=render_json,
        check=fake_check(),
        seed=7,
    )


@pytest.mark.asyncio
async def test_meeting_everyone_pitches_votes_never_for_self_and_lead_feeds_writers(tmp_path):
    # a、b 都投 c，c 投 a：c 当主案
    sc = TeamSC(votes={"a": "c", "b": "c", "c": "a"})
    bb = make(tmp_path, sc)
    bb.submit("做一个修图台")
    await bb.wait()
    b = bb.build
    m = b["meeting"]
    pitchers = [c["who"] for c in sc.calls if c["role"] == "bench_pitch"]
    assert sorted(pitchers) == ["a", "b", "c"]  # 每人一份，不重复找同一个人
    assert sorted(p["label"] for p in m["pitches"]) == ["A", "B", "C"]
    assert m["lead"] == "c"
    votes = {v["voter"]: v["pick"] for v in m["votes"]}
    by_label = {p["label"]: p["worker"] for p in m["pitches"]}
    assert all(by_label[pick] != voter for voter, pick in votes.items())
    # 投票的人看不到自己的方案
    for c, raw in zip(
        [c for c in sc.calls if c["role"] == "bench_vote"],
        [x for x in sc.prompts if json.loads(x)["role"] == "bench_vote"],
        strict=True,
    ):
        assert f"{c['who']} 的思路" not in json.loads(raw)["pitches"]
        assert c["preset"] == c["who"]
    # 写窗口的人拿到主案的做法和大家建议补的点
    window = next(json.loads(x) for x in sc.prompts if json.loads(x)["role"] == "bench_window")
    assert "方案会定下的主案" in window["sources"] and "c 做法一" in window["sources"]
    assert "补进来：a 建议补撤销" in window["sources"]
    assert "a 做法一" not in window["sources"]
    # 方案会在拆需求之后、写窗口之前
    roles = [c["role"] for c in sc.calls]
    assert roles.index("bench_plan") < roles.index("bench_pitch") < roles.index("bench_window")
    # 协作看板：每个人的角色都记下来了
    cards = b["team"]["cards"]
    assert {c["role"] for c in cards} >= {"拆需求", "主案候选", "投票", "实施", "评审", "对抗"}
    assert all(c["ok"] for c in cards)
    assert any("主案" in t["title"] or "方案会" in t["title"] for t in b["timeline"])


@pytest.mark.asyncio
async def test_meeting_tie_is_drawn_and_unreadable_pitch_does_not_count(tmp_path):
    sc = TeamSC(broken=("b",), votes={"a": "c", "c": "a"})  # 两份能用，各得 1 票
    bb = make(tmp_path, sc)
    bb.submit("做一个麻将台")
    await bb.wait()
    m = bb.build["meeting"]
    assert sorted(p["worker"] for p in m["pitches"]) == ["a", "c"]
    assert m["lead"] in ("a", "c")
    assert all(p["votes"] == 1 for p in m["pitches"])
    assert any("看不懂" in x["text"] for x in bb.build["log"])


@pytest.mark.asyncio
async def test_single_assistant_pitch_wins_without_vote(tmp_path):
    sc = TeamSC(workers=("a",))
    bb = make(tmp_path, sc, team={"attack": False})
    bb.submit("做一个跑团台")
    await bb.wait()
    m = bb.build["meeting"]
    assert m["lead"] == "a" and m["votes"] == []
    assert "bench_vote" not in [c["role"] for c in sc.calls]


@pytest.mark.asyncio
async def test_attacker_avoids_authors_and_high_bug_blocks_pass_until_fixed(tmp_path):
    bugs = {
        1: [
            {
                "window": "plan",
                "severity": "high",
                "problem": "空行程点保存会卡死",
                "steps": "清空",
            },
            {"window": "不存在", "severity": "怪", "problem": "按钮太小"},
            {"severity": "low", "problem": ""},  # 没写问题：丢掉
        ],
        2: [],
    }
    sc = TeamSC(workers=("a", "b", "c", "d"), bugs=lambda n: bugs.get(n, []))
    bb = make(tmp_path, sc, team={"pitches": 1})
    bb.submit("做一个行程台")
    await bb.wait()
    b = bb.build
    r1, r2 = b["rounds"][0], b["rounds"][1]
    a1 = r1["attack"]
    assert [x["severity"] for x in a1["bugs"]] == ["high", "mid"]
    assert a1["bugs"][1]["window"] == ""
    # 第 1 轮评审全过，但有严重问题：不算过线，返工的人拿到对抗手的问题
    assert not r1["passed"] and r2["passed"] and b["status"] == "done"
    rework = [json.loads(x) for x in sc.prompts if json.loads(x)["role"] == "bench_rework"]
    plan_rework = next(x for x in rework if x["file"].endswith("plan.js"))
    assert "[对抗·必须修] 空行程点保存会卡死" in plan_rework["problems"]
    # 对抗手先找没写窗口的人
    attackers = [c for c in sc.calls if c["role"] == "bench_attack"]
    assert attackers[0]["exclude"] == tuple(sorted({w["author"] for w in b["windows"]}))
    assert attackers[0]["who"] not in attackers[0]["exclude"]
    assert any("严重问题，修完才算过线" in x["text"] for x in b["log"])


@pytest.mark.asyncio
async def test_attacker_falls_back_to_authors_when_everyone_wrote(tmp_path):
    sc = TeamSC(workers=("a", "b"), bugs=lambda n: [])
    bb = make(tmp_path, sc, team={"pitches": 1})
    bb.submit("做一个记账台")
    await bb.wait()
    calls = [c for c in sc.calls if c["role"] == "bench_attack"]
    assert calls[0]["exclude"] == ("a", "b")
    assert len(calls) == 2 and calls[1]["exclude"] == ()  # 第一次没人，才放开
    assert bb.build["rounds"][0]["attack"]["worker"] in ("a", "b")


@pytest.mark.asyncio
async def test_team_off_keeps_old_flow(tmp_path):
    sc = TeamSC()
    bb = BenchBuilder(
        BenchRules(), root=tmp_path, supercomputer=sc, render=render_json, check=fake_check()
    )
    bb.submit("旅行台")
    await bb.wait()
    roles = {c["role"] for c in sc.calls}
    assert not roles & {"bench_pitch", "bench_vote", "bench_attack"}
    assert bb.build["meeting"] is None and "attack" not in bb.build["rounds"][0]


class PluginResearcher(FakeResearcher):
    async def run(self, queries, news):
        from roundtable.core.benchkit.research import Item

        rep = await super().run(queries, news)
        rep.plugins = [
            Item("dsh", "x/dsh-canvas", "https://github.com/x/dsh-canvas", stars=40),
            Item("dsh", "不是仓库名", "https://example.com/p", stars=90),
        ]
        return rep


@pytest.mark.asyncio
async def test_dsh_plugin_gets_a_learning_card_and_can_be_requested(tmp_path):
    sc = ResearchSC()
    scout = LearnScout()
    bb = BenchBuilder(
        BenchRules(),
        root=tmp_path,
        supercomputer=sc,
        render=render_json,
        check=fake_check(),
        scout=scout,
        researcher=PluginResearcher(),
        whitelist=lambda: {"x/dsh-canvas": "canvas"},
        seed=7,
    )
    bb.submit("修图台")
    await bb.wait()
    plugins = bb.build["research"]["plugins"]
    assert [p["whitelisted"] for p in plugins] == ["canvas", ""]
    cards = bb.build["research"]["cards"]
    plug = [c for c in cards if c["plugin"]]
    assert [c["repo"] for c in plug] == ["x/dsh-canvas"]
    assert plug[0]["verdict"] == "idea"  # 许可证没核对：只借思路
    assert "x/dsh-canvas" in scout.fetched
    bid = bb.build["id"]
    got = bb.request_plugin(bid, "x/dsh-canvas")
    bb.request_plugin(bid, "x/dsh-canvas")  # 点两次只记一次
    assert [r["name"] for r in got["plugin_requests"]] == ["x/dsh-canvas"]
    allreq = json.loads((tmp_path / "plugin_requests.json").read_text(encoding="utf-8"))
    assert [r["name"] for r in allreq] == ["x/dsh-canvas"]
    saved = json.loads((tmp_path / bid / "build.json").read_text(encoding="utf-8"))
    assert saved["plugin_requests"][0]["name"] == "x/dsh-canvas"
    with pytest.raises(ValueError):
        bb.request_plugin(bid, "没有这个")
    with pytest.raises(KeyError):
        bb.request_plugin("nope", "x/dsh-canvas")
