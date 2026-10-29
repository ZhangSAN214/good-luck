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
            (folder / p["file"]).write_text("export default (el) => { el.textContent = 'ok'; };")
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
