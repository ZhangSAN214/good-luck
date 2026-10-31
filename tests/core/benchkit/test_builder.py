"""工作台生成器的流程：拆需求 → 分头写窗口 → 质检 + 评审（不自评）→ 返工 → 过线或停下来等你。

超级电脑和质检都换成假的：不跑命令行、不开浏览器。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtable.core.benchkit.builder import BenchBuilder, merge_scores, passes, review_groups
from roundtable.core.benchkit.quality import Finding, Report
from roundtable.core.benchkit.spec import SpecError, normalize, shell_html
from roundtable.core.config.schema import BenchRules
from roundtable.core.supercomputer.runner import RunResult

PLAN = {
    "title": "旅行台",
    "subtitle": "三天东京行程",
    "icon": "plane",
    "theme": "ocean",
    "windows": [
        {
            "id": "Map View",
            "title": "地图",
            "span": 8,
            "parts": ["mapFrame", "不存在的零件"],
            "icon": "map",
        },
        {"id": "plan", "title": "行程", "span": 4, "parts": ["timeline"], "icon": "没有这个图标"},
        {"id": "budget", "title": "预算", "span": 20, "parts": ["donut", "table"]},
    ],
    "needs": [{"need": "真地图", "keywords": "leaflet map"}],
}


def render(role, **values):
    return json.dumps({"role": role, **values}, ensure_ascii=False)


class W:
    def __init__(self, wid):
        self.id = wid


class FakeSC:
    """假的超级电脑：按角色回话；写窗口时真的往文件夹里写文件。按 kind 轮流挑人。"""

    def __init__(self, workers=("a", "b", "c"), scores=None, plan=PLAN):
        self.workers = list(workers)
        self.scores = scores or (lambda n: 9)
        self.plan = plan
        self.calls: list[dict] = []
        self.prompts: list[str] = []
        self.review_n = 0
        self.turn = 0
        self.parts: dict[str, str] = {}
        self.window_code = "export default (el, ctx) => { el.innerHTML = ctx.kit.esc('ok'); };"

    def worker(self, wid):
        return W(wid) if wid in self.workers else None

    def state(self, w):
        from roundtable.core.supercomputer.scoring import WorkerState

        return WorkerState(used=0, limit=20, running=0, blocked=None)

    async def step(self, kind, prompt, *, folder, rng, record, log=None, preset=None, exclude=()):
        self.prompts.append(prompt)
        p = json.loads(prompt)
        pool = [w for w in self.workers if w not in exclude]
        if not pool:
            return None, None
        who = preset if preset in pool else pool[self.turn % len(pool)]
        self.turn += 1
        self.calls.append(
            {"role": p["role"], "who": who, "exclude": exclude, "preset": preset, "folder": folder}
        )
        record["worker"] = who
        if p["role"] == "bench_plan":
            return W(who), RunResult(0, json.dumps(self.plan, ensure_ascii=False))
        if p["role"] in ("bench_window", "bench_rework"):
            (folder / p["file"]).write_text(self.window_code)
            (folder / p["data_file"]).write_text("{}")
            for name, text in self.parts.items():
                (folder / "parts").mkdir(exist_ok=True)
                (folder / "parts" / name).write_text(text, encoding="utf-8")
            return W(who), RunResult(0, f"{who} 写好了 {p['file']}")
        if p["role"] == "bench_review":
            self.review_n += 1
            s = self.scores(self.review_n)
            wids = [line[2:].split("（")[0] for line in p["windows"].splitlines()]
            out = {
                "scores": {k: s for k in p["rubric"].split("、")},
                "windows": [{"id": wids[0], "problems": ["标题太挤"], "fixes": ["加大间距"]}],
                "summary": "还行",
            }
            return W(who), RunResult(0, json.dumps(out, ensure_ascii=False))
        if p["role"] == "bench_ask":
            return W(who), RunResult(0, f"回答：{p['question']}")
        if p["role"] == "bench_ask_json":
            return W(who), RunResult(0, json.dumps({"days": [{"id": "d1", "q": p["question"]}]}))
        raise AssertionError(p["role"])


def fake_check(ok=True):
    def check(target, out_dir=None, **kw):
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        shot = out / "电脑_light.png"
        shot.write_bytes(b"png")
        r = Report(target=target, shots=[str(shot)], combos=1)
        if not ok:
            r.findings = [Finding("横向溢出", "block", "手机·浅色", "宽 500px")]
        return r

    return check


def make(tmp_path, sc, check=None, **rules):
    return BenchBuilder(
        BenchRules(**rules),
        root=tmp_path,
        supercomputer=sc,
        render=render,
        check=check or fake_check(),
        seed=7,
    )


def test_normalize_fixes_ids_parts_icons_and_span():
    spec = normalize(PLAN, max_windows=8)
    assert [w.id for w in spec.windows] == ["map-view", "plan", "budget"]
    assert spec.windows[0].parts == ["mapFrame"]
    assert spec.windows[1].icon == "layout-grid"  # 不认得的图标换成默认
    assert spec.windows[2].span == 12
    assert spec.theme == "ocean" and spec.icon == "plane"
    assert normalize(PLAN, max_windows=2).windows[-1].id == "plan"
    with pytest.raises(SpecError):
        normalize({"title": "x", "windows": []}, max_windows=8)


def test_shell_escapes_and_mounts_each_window():
    spec = normalize({**PLAN, "title": "<b>坏</b>"}, max_windows=8)
    html = shell_html(spec, "id-1")
    assert "<b>坏</b>" not in html and "&lt;b&gt;" in html
    for w in spec.windows:
        assert f'data-window="{w.id}"' in html
    assert '<link rel="stylesheet" href="/kit/kit.css">' in html
    assert "%%" not in html


def test_scores_and_groups():
    rules = BenchRules()
    assert passes(True, {"a": 8, "b": 9}, rules)
    assert not passes(True, {"a": 10, "b": 5}, rules)  # 有一项太低
    assert not passes(False, {"a": 10}, rules)  # 质检没过
    assert not passes(True, {}, rules)  # 没评分不算过
    assert merge_scores([{"x": 8}, {"x": 6}], ["x", "y"]) == {"x": 7, "y": 0}
    assert review_groups({"w1": "a", "w2": "b", "w3": "a"}) == {"a": ["w1", "w3"], "b": ["w2"]}


async def test_build_passes_first_round(tmp_path):
    sc = FakeSC()
    b = make(tmp_path, sc)
    build = b.submit("帮我做个东京三天旅行的工作台")
    await b.wait()
    assert build["status"] == "done", build["log"]
    folder = tmp_path / build["id"]
    assert (folder / "index.html").is_file() and (folder / "spec.json").is_file()
    for w in build["windows"]:
        assert (folder / "windows" / f"{w['id']}.js").is_file()
        assert w["author"] in sc.workers
    assert len(build["rounds"]) == 1 and build["rounds"][0]["passed"]
    assert build["score"] == 9
    saved = json.loads((folder / "build.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done"
    assert b.recent()[0]["id"] == build["id"]


async def test_reviewers_never_review_their_own_windows(tmp_path):
    sc = FakeSC()
    b = make(tmp_path, sc)
    build = b.submit("学习台")
    await b.wait()
    reviews = [c for c in sc.calls if c["role"] == "bench_review"]
    assert reviews
    for c in reviews:
        assert c["who"] not in c["exclude"] and len(c["exclude"]) == 1
    for rec in build["rounds"][0]["reviews"]:
        assert rec["worker"] != rec["author"]


async def test_low_scores_trigger_rework_by_author_then_pass(tmp_path):
    sc = FakeSC(scores=lambda n: 5 if n <= 3 else 9)
    b = make(tmp_path, sc)
    build = b.submit("拍摄台")
    await b.wait()
    assert build["status"] == "done", build["log"]
    assert len(build["rounds"]) == 2 and not build["rounds"][0]["passed"]
    reworks = [c for c in sc.calls if c["role"] == "bench_rework"]
    assert reworks and all(c["preset"] == c["who"] for c in reworks)  # 交回作者本人


async def test_stops_and_waits_for_you_after_max_rounds(tmp_path):
    sc = FakeSC()
    b = make(tmp_path, sc, check=fake_check(ok=False), max_rounds=2)
    build = b.submit("看板台")
    await b.wait()
    assert build["status"] == "needs_you"
    assert len(build["rounds"]) == 2
    assert all(not r["passed"] for r in build["rounds"])
    rework = [c for c in sc.calls if c["role"] == "bench_rework"]
    assert len(rework) == 3  # 每个窗口都带着质检问题返工一次（第 2 轮后不再返工）


async def test_single_worker_cannot_review_so_never_passes(tmp_path):
    sc = FakeSC(workers=("solo",))
    b = make(tmp_path, sc, max_rounds=1)
    build = b.submit("一个人干")
    await b.wait()
    assert build["status"] == "needs_you"
    assert all("error" in r for r in build["rounds"][0]["reviews"])


async def test_bad_plan_fails_cleanly(tmp_path):
    sc = FakeSC(plan={"title": "x"})
    b = make(tmp_path, sc)
    build = b.submit("坏的")
    await b.wait()
    assert build["status"] == "failed"
    assert "看不懂" in build["log"][-1]["text"]


async def test_submit_rules(tmp_path):
    b = make(tmp_path, FakeSC())
    with pytest.raises(ValueError):
        b.submit("  ")
    b.submit("一")
    with pytest.raises(RuntimeError):
        b.submit("二")
    await b.cancel()
    assert b.build["status"] == "cancelled"
    off = BenchBuilder(
        BenchRules(enabled=False), root=tmp_path, supercomputer=FakeSC(), render=render
    )
    with pytest.raises(ValueError):
        off.submit("x")
    assert off.get("../etc") is None


GOOD_PART = "// part: route-line — 在地图上画路线\nexport function routeLine() {}\n"


async def test_new_parts_join_library_only_after_passing(tmp_path):
    sc = FakeSC()
    sc.parts = {
        "route-line.js": GOOD_PART,
        "no-header.js": "export const x = 1;\n",  # 没写开头那行：不收
        "wrong-name.js": "// part: other-name — 名字和文件对不上\n",
    }
    b = make(tmp_path, sc, check=fake_check(ok=False), max_rounds=1)
    b.submit("没过线的台")
    await b.wait()
    assert b.build["status"] == "needs_you"
    assert b.library.parts() == []  # 没过线，零件不收

    b2 = make(tmp_path, sc)
    build = b2.submit("过线的台")
    await b2.wait()
    assert build["status"] == "done"
    assert build["new_parts"] == ["route-line"]
    assert [x["name"] for x in b2.library.parts()] == ["route-line"]
    kept = (tmp_path / "_library" / "parts" / "route-line.js").read_text(encoding="utf-8")
    assert kept.startswith("// part: route-line") and build["id"] in kept
    recipe = json.loads((tmp_path / "_library" / "recipes" / build["recipe"]).read_text("utf-8"))
    assert recipe["request"] == "过线的台" and recipe["spec"]["title"] == "旅行台"
    assert b2.status()["library"]["parts"][0]["name"] == "route-line"
    assert b2.status()["library"]["recipes"][0]["bench"] == build["id"]
    assert all(r["id"] != "_library" for r in b2.recent())


async def test_library_is_seeded_into_new_bench_and_shown_to_planner(tmp_path):
    lib = tmp_path / "_library" / "parts"
    lib.mkdir(parents=True)
    (lib / "route-line.js").write_text(GOOD_PART, encoding="utf-8")
    sc = FakeSC()
    b = make(tmp_path, sc)
    build = b.submit("又一个旅行台")
    await b.wait()
    assert (tmp_path / build["id"] / "parts" / "route-line.js").is_file()
    plan_prompt = json.loads(sc.prompts[0])
    assert "route-line：在地图上画路线" in plan_prompt["parts"]
    assert build["new_parts"] == []  # 本来就在库里的不算新的


async def test_ask_uses_spec_and_data_and_validates(tmp_path):
    sc = FakeSC()
    b = make(tmp_path, sc)
    build = b.submit("旅行台")
    await b.wait()
    assert await b.ask(build["id"], " 第一天去哪 ") == "回答：第一天去哪"
    p = json.loads(sc.prompts[-1])
    assert p["role"] == "bench_ask" and "旅行台" in p["spec"] and "## " in p["data"]
    assert sc.calls[-1]["folder"] == tmp_path / build["id"] / "ask"  # 在子文件夹里答，不碰台本身
    with pytest.raises(ValueError):
        await b.ask(build["id"], "  ")
    with pytest.raises(ValueError):
        await b.ask(build["id"], "长" * 2000)
    with pytest.raises(LookupError):
        await b.ask("nope", "在吗")
    sc.workers = []
    assert "没有助手" in await b.ask(build["id"], "在吗")


async def test_window_without_kit_is_sent_back_to_its_author_only(tmp_path):
    sc = FakeSC()
    sc.window_code = "export default (el) => { el.textContent = 'ok'; };"
    b = make(tmp_path, sc, max_rounds=2)
    build = b.submit("手写台")
    await b.wait()
    assert build["status"] == "needs_you"
    f = next(x for x in build["rounds"][0]["check"]["findings"] if x["check"] == "窗口没用零件库")
    assert f["severity"] == "block" and set(f["windows"]) == {w["id"] for w in build["windows"]}
    reworks = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_rework"]
    assert reworks and all("窗口没用零件库" in p["problems"] for p in reworks)
    assert all("必须修" in p["problems"] for p in reworks)


def test_check_findings_route_to_named_windows():
    b = BenchBuilder(BenchRules(), root=None, supercomputer=FakeSC(), render=render)
    rnd = {
        "check": {
            "findings": [
                {
                    "check": "窗口太长",
                    "severity": "block",
                    "where": "手机·浅色",
                    "detail": "list 5 屏",
                    "windows": ["list"],
                },
                {
                    "check": "页面报错",
                    "severity": "block",
                    "where": "电脑·浅色",
                    "detail": "boom",
                    "windows": [],
                },
            ]
        },
        "reviews": [],
    }
    assert "窗口太长" in b._problems_for(rnd, "list")
    other = b._problems_for(rnd, "map")
    assert "窗口太长" not in other and "页面报错" in other


def test_spec_keeps_shared_facts_and_event_contract(tmp_path):
    from roundtable.core.benchkit.spec import write_shell

    raw = dict(PLAN)
    raw["shared"] = {
        "dates": "2026-11-03 至 11-05",
        "people": [{"role": "孩子", "age": 6}],
        "currency": "JPY",
    }
    raw["events"] = [
        {
            "name": "Trip Changed",
            "from": "Map View",
            "to": ["plan", "nope"],
            "detail": {"people": "人数"},
            "when": "改人数",
        },
        {"from": "plan"},
    ]
    spec = normalize(raw, max_windows=8)
    assert spec.shared["currency"] == "JPY"
    assert spec.events == [
        {
            "name": "trip-changed",
            "from": "map-view",
            "to": ["plan"],
            "detail": {"people": "人数"},
            "when": "改人数",
        }
    ]
    write_shell(tmp_path, spec, "b1")
    assert json.loads((tmp_path / "data" / "_shared.json").read_text("utf-8"))["currency"] == "JPY"
    raw["shared"] = {"x": "长" * 7000}
    with pytest.raises(SpecError):
        normalize(raw, max_windows=8)


async def test_selftest_sends_broken_window_back_before_review(tmp_path):
    calls = []

    def check(target, out_dir=None, **kw):
        calls.append((Path(out_dir).name, kw.get("viewports")))
        r = fake_check()(target, out_dir, **kw)
        if Path(out_dir).name == "selftest":
            r.findings = [
                Finding("页面报错", "block", "电脑·浅色", "窗口 plan 出错：SyntaxError", ["plan"]),
                Finding("字太小", "warn", "电脑·浅色", "11px", ["budget"]),  # 自测只管低级错误
            ]
        return r

    sc = FakeSC()
    b = make(tmp_path, sc, check=check)
    build = b.submit("旅行台")
    await b.wait()
    assert build["status"] == "done", build["log"]
    assert calls[0][0] == "selftest" and len(calls[0][1]) == 1  # 自测只开一种屏宽
    roles = [json.loads(p)["role"] for p in sc.prompts]
    first_review = roles.index("bench_review")
    reworks = [
        json.loads(p) for p in sc.prompts[:first_review] if json.loads(p)["role"] == "bench_rework"
    ]
    assert len(reworks) == 1 and "SyntaxError" in reworks[0]["problems"]
    assert reworks[0]["file"] == "windows/plan.js"
    assert any("自测" in x["text"] for x in build["log"])


def test_spec_keeps_shared_user_data_keys_and_shell_passes_state():
    raw = dict(PLAN)
    raw["state"] = {"itinerary": "条目：id、标题、费用", "my key!": "x", "坏": "y", "n": 1}
    spec = normalize(raw, max_windows=8)
    assert spec.state == {"itinerary": "条目：id、标题、费用", "mykey": "x", "n": "1"}
    assert "state, bus, ask" in shell_html(spec, "b1")
    raw["state"] = {f"k{i}": "x" for i in range(30)}
    assert len(normalize(raw, max_windows=8).state) == 10


async def test_reviewers_see_last_rounds_problems_for_their_windows(tmp_path):
    sc = FakeSC(scores=lambda n: 5 if n <= 3 else 9)
    b = make(tmp_path, sc)
    b.submit("拍摄台")
    await b.wait()
    reviews = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_review"]
    assert all("第一轮" in r["previous"] for r in reviews[:3])
    later = [r for r in reviews[3:] if "标题太挤" in r["previous"]]
    assert later  # 第 2 轮评审先核对上一轮提的问题
    for r in later:
        wid = r["previous"].split("：")[0][2:]
        assert f"- {wid}（" in r["windows"]  # 只给自己这组窗口的


BASE = ["主视觉占大半屏", "不自己画插图"]


def test_requirements_number_user_asks_and_take_baseline_from_config():
    raw = dict(PLAN)
    raw["hero"] = "Map View"
    raw["requirements"] = [
        {"text": "能按婚礼定制", "check": "选婚礼后出现宾客接送", "windows": ["plan", "nope"]},
        {"text": ""},
        {"id": "b1", "text": "改掉的底线", "check": "看地图那格", "windows": ["map-view"]},
    ]
    spec = normalize(raw, max_windows=8, baseline=BASE, max_requirements=4)
    assert [r["id"] for r in spec.requirements] == ["r1", "b1", "b2"]
    r1, b1, b2 = spec.requirements
    assert r1["windows"] == ["plan"] and r1["source"] == "你的要求"
    assert b1["text"] == "主视觉占大半屏" and b1["check"] == "看地图那格"  # 底线原文以配置为准
    assert b2["windows"] == [] and b2["source"] == "底线"
    # 主视觉排最前、至少 8 列，外壳给它加 k-hero
    assert spec.hero == "map-view" and spec.windows[0].id == "map-view"
    assert spec.windows[0].span == 8
    assert "k-span-8 k-hero" in shell_html(spec, "b1")
    raw["hero"] = "nope"
    assert normalize(raw, max_windows=8).hero == ""


class CheckSC(FakeSC):
    """评审逐条验收：unmet 里的要求判为没做到（直到第 fix_after 次评审）。"""

    def __init__(self, unmet=(), fix_after=99, **kw):
        super().__init__(**kw)
        self.unmet, self.fix_after = set(unmet), fix_after

    async def step(self, kind, prompt, **kw):
        chosen, result = await super().step(kind, prompt, **kw)
        p = json.loads(prompt)
        if p["role"] == "bench_review":
            out = json.loads(result.output)
            ids = [line[2:].split("（")[0] for line in p["requirements"].splitlines()]
            bad = self.unmet if self.review_n <= self.fix_after else set()
            out["checks"] = [
                {"id": i, "met": i not in bad, "why": "只换了标签" if i in bad else ""} for i in ids
            ]
            return chosen, RunResult(0, json.dumps(out, ensure_ascii=False))
        return chosen, result


def plan_with_requirements():
    raw = dict(PLAN)
    raw["requirements"] = [{"text": "能按婚礼定制", "check": "选婚礼", "windows": ["plan"]}]
    return raw


async def test_unmet_requirement_blocks_pass_and_goes_to_its_window(tmp_path):
    sc = CheckSC(unmet={"r1"}, fix_after=3, plan=plan_with_requirements())
    b = make(tmp_path, sc, baseline=BASE)
    build = b.submit("旅行台")
    await b.wait()
    assert build["status"] == "done", build["log"]
    first, second = build["rounds"]
    assert not first["passed"]  # 分数 9，但 r1 没做到
    assert [r["id"] for r in first["requirements"] if not r["met"]] == ["r1"]
    assert first["requirements"][0]["why"] == ["只换了标签"]
    assert second["passed"] and all(r["met"] for r in second["requirements"])
    reviews = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_review"]
    assert "r1（你的要求）能按婚礼定制｜检查：选婚礼｜负责窗口：plan" in reviews[0]["requirements"]
    assert "b2（底线）不自己画插图" in reviews[0]["requirements"]
    reworks = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_rework"]
    plan_fix = [r for r in reworks if r["file"] == "windows/plan.js"]
    assert plan_fix and "[验收·必须做到·r1] 能按婚礼定制：只换了标签" in plan_fix[0]["problems"]
    others = [r for r in reworks if r["file"] != "windows/plan.js"]
    assert all("r1" not in r["problems"] for r in others)
    assert any("验收：2/3" in line for line in (x["text"] for x in build["log"]))


def test_merge_checks_needs_every_judge_and_no_missing():
    reqs = [{"id": i, "source": "s", "text": i, "windows": []} for i in ("r1", "r2", "r3")]
    reviews = [
        {"checks": {"r1": {"met": True, "why": ""}, "r2": {"met": True, "why": ""}}},
        {"checks": {"r1": {"met": False, "why": "没有"}}},
        {"error": "没评成"},
    ]
    from roundtable.core.benchkit.builder import merge_checks

    got = {r["id"]: r for r in merge_checks(reqs, reviews)}
    assert not got["r1"]["met"] and got["r1"]["why"] == ["没有"]
    assert got["r2"]["met"]
    assert not got["r3"]["met"]  # 没人判过不算做到


async def test_window_whose_score_drops_is_reverted_and_reworked_from_last_advice(tmp_path):
    # 第 1 轮 7 分，第 2 轮 5 分（改坏了），第 3 轮 9 分
    sc = FakeSC(scores=lambda n: 7 if n <= 3 else (5 if n <= 6 else 9))
    versions = iter(range(100))

    def code():
        return (
            f"export default (el, ctx) => {{ el.innerHTML = ctx.kit.esc('v{next(versions)}'); }};"
        )

    orig = sc.step

    async def step(kind, prompt, **kw):
        sc.window_code = code()
        return await orig(kind, prompt, **kw)

    sc.step = step
    b = make(tmp_path, sc)
    build = b.submit("拍摄台")
    await b.wait()
    assert build["status"] == "done", build["log"]
    second = build["rounds"][1]
    assert set(second["reverted"]) == {"map-view", "plan", "budget"}
    assert second["reverted"]["plan"] == [7.0, 5.0]
    assert any("退回上一版" in line for line in (x["text"] for x in build["log"]))
    folder = tmp_path / build["id"]
    snap = folder / "report" / "round-1" / "snap" / "windows" / "plan.js"
    assert snap.is_file()
    reworks = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_rework"]
    last = [r for r in reworks if r["file"] == "windows/plan.js"][-1]
    assert last["problems"].startswith("- [退回] 上一次的改法让分数从 7 降到 5")


async def test_ask_json_returns_parsed_data_or_reason(tmp_path):
    sc = FakeSC()
    b = make(tmp_path, sc)
    build = b.submit("旅行台")
    await b.wait()
    orig = sc.step

    async def step(kind, prompt, **kw):
        p = json.loads(prompt)
        if p["role"] == "bench_ask_json":
            sc.calls.append({"role": p["role"]})
            body = '好的：{"days": [{"id": "d1"}]}' if "行程" in p["question"] else "做不了"
            return W("a"), RunResult(0, body)
        return await orig(kind, prompt, **kw)

    sc.step = step
    got = await b.ask_json(build["id"], "按杭州 3 天生成行程")
    assert got["data"] == {"days": [{"id": "d1"}]}
    bad = await b.ask_json(build["id"], "别的")
    assert bad["data"] is None and bad["text"] == "做不了"


async def test_net_list_is_written_for_the_shell_and_shown_to_writers(tmp_path):
    net = [
        {
            "name": "tiles",
            "use": "地图底图",
            "url": "https://t.example/{z}/{x}/{y}.png",
            "source": "示例地图",
            "kind": "tiles",
        },
        {
            "name": "weather",
            "use": "天气，参数 lat、lng",
            "url": "https://w.example/f?a={lat}",
            "source": "示例天气",
            "also": ["https://img.example/"],
        },
    ]
    seen = []

    def check(target, out_dir=None, **kw):
        seen.append(kw.get("allow"))
        return fake_check()(target, out_dir)

    sc = FakeSC()
    b = make(tmp_path, sc, check=check, net=net)
    build = b.submit("旅行台")
    await b.wait()
    written = json.loads((tmp_path / build["id"] / "data" / "_net.json").read_text("utf-8"))
    assert [x["name"] for x in written] == ["tiles", "weather"]
    assert written[1]["ttl"] == 60 and "also" not in written[1]
    assert seen and all(
        a == ("https://t.example", "https://w.example", "https://img.example") for a in seen
    )
    windows = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_window"]
    assert "ctx.net.get('weather', 参数)" in windows[0]["net"]
    assert "地图零件 mapFrame 自动使用" in windows[0]["net"]


def test_net_services_must_be_keyless_https():
    from pydantic import ValidationError

    from roundtable.core.config.schema import BenchNetService

    ok = dict(name="fx", use="汇率", url="https://fx.example/l?from={a}", source="央行")
    assert BenchNetService(**ok).origins == ["https://fx.example"]
    for bad in (
        {"url": "http://fx.example/x"},
        {"url": "https://fx.example/x?api_key={k}"},
        {"also": ["https://x.example/?token=1"]},
    ):
        with pytest.raises(ValidationError):
            BenchNetService(**(ok | bad))


# --- 开工前调研 ----------------------------------------------------------------------


class ResearchSC(FakeSC):
    """调研助手给搜索词和资讯；学习卡按仓库名写。"""

    async def step(self, kind, prompt, **kw):
        p = json.loads(prompt)
        if p["role"] == "bench_research":
            kw["record"]["worker"] = "c"
            self.calls.append({"role": p["role"], "who": "c", "kind": kind})
            out = {
                "queries": ["travel planner"],
                "news": [{"title": "发布会", "url": "https://x/ok", "kind": "launch"}],
            }
            return W("c"), RunResult(0, json.dumps(out, ensure_ascii=False))
        if p["role"] == "bench_learn":
            r = json.loads(p["repo"])
            self.calls.append({"role": p["role"], "who": "c", "folder": p["folder"]})
            card = {"what": f"{r['title']} 的做法", "borrow": ["时间轴拖拽"], "avoid": ["要服务器"]}
            return W("c"), RunResult(0, json.dumps(card, ensure_ascii=False))
        return await super().step(kind, prompt, **kw)


class FakeResearcher:
    def __init__(self):
        self.queries = None

    async def run(self, queries, news):
        from roundtable.core.benchkit.research import Item, Report

        self.queries = queries
        rep = Report(queries=queries)
        rep.github = [
            Item(
                "github",
                f"o/r{i}",
                f"https://github.com/o/r{i}",
                stars=1000 - i,
                verdict="adopt" if i % 2 else "idea",
                license="MIT",
            )
            for i in range(10)
        ]
        rep.papers = [
            Item(
                "paper",
                "Trip Planning",
                "https://arxiv.org/abs/1",
                source="arXiv",
                date="2024-01-01",
            )
        ]
        rep.news = [Item("launch", n["title"], n["url"], verified=True) for n in news]
        return rep


class LearnScout:
    def __init__(self):
        self.fetched = []

    async def fetch_json(self, url):
        return {"default_branch": "main"}

    async def fetch(self, c, dest):
        if c.repo == "o/r1":
            raise ValueError("太大")
        dest.mkdir(parents=True, exist_ok=True)
        self.fetched.append(c.repo)
        return {}

    async def search(self, need, keywords):
        return []


async def test_research_runs_before_writing_and_feeds_learning_cards(tmp_path):
    sc = ResearchSC()
    res = FakeResearcher()
    scout = LearnScout()
    b = BenchBuilder(
        BenchRules(),
        root=tmp_path,
        supercomputer=sc,
        render=render,
        check=fake_check(),
        scout=scout,
        researcher=res,
        seed=7,
    )
    b.submit("帮我做一个旅行台")
    await b.wait()
    build = b.build
    roles = [c["role"] for c in sc.calls]
    assert roles.index("bench_research") < roles.index("bench_window")
    assert res.queries[0] == "travel planner" and "leaflet map" in res.queries
    r = build["research"]
    assert r["counts"]["github"] == 10 and r["worker"] == "c"
    # 学习卡：星最多的 3 个；下载失败的照样按简介写
    assert [c["repo"] for c in r["cards"]] == ["o/r0", "o/r1", "o/r2"]
    assert scout.fetched == ["o/r0", "o/r2"]
    assert r["cards"][1]["folder"] == "" and r["cards"][0]["folder"] == "research/learn/o__r0"
    folders = [c["folder"] for c in sc.calls if c["role"] == "bench_learn"]
    assert "（没有源码，只按简介写）" in folders
    # 写窗口的助手拿到学习卡和论文
    window = next(json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_window")
    assert "学习卡 o/r0" in window["sources"] and "借：时间轴拖拽" in window["sources"]
    assert "论文：Trip Planning" in window["sources"]
    md = (tmp_path / build["id"] / "调研报告.md").read_text(encoding="utf-8")
    assert "GitHub 10 个" in md and "发布会" in md
    assert json.loads((tmp_path / build["id"] / "research.json").read_text(encoding="utf-8"))
    assert any("调研：GitHub 10 个" in x["text"] for x in build["log"])


async def test_research_off_or_no_researcher_skips_it(tmp_path):
    sc = ResearchSC()
    b = BenchBuilder(
        BenchRules(research={"enabled": False}),
        root=tmp_path,
        supercomputer=sc,
        render=render,
        check=fake_check(),
        researcher=FakeResearcher(),
        seed=7,
    )
    b.submit("旅行台")
    await b.wait()
    assert b.build["research"] is None
    assert "bench_research" not in [c["role"] for c in sc.calls]
