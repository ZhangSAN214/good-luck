"""应用型产出（K1）、编辑器底座（K2）、企业级验收（K5）：规格、外壳、清单、生成流程。

超级电脑和验收都是假的：不跑命令行、不开浏览器（真浏览器的测试在 tests/web/test_kit_editor.py）。
"""

from __future__ import annotations

import json
from pathlib import Path

from roundtable.core.benchkit.appcheck import CHECK_NAMES, FEATURE_FAIL, AppReport, findings_of
from roundtable.core.benchkit.quality import KIT_DIR, Finding
from roundtable.core.benchkit.spec import (
    BASES,
    base_api,
    load_spec,
    normalize,
    shell_html,
    write_app_manifest,
    write_shell,
)
from roundtable.core.config.loader import load_config

from .test_builder import FakeSC, make

ROOT = Path(__file__).resolve().parents[3]
PHOTO = json.loads((ROOT / "tests" / "web" / "photo_bench" / "plan.json").read_text("utf-8"))
APPS = {"editor": "editor"}
KINDS = {"editor", "dashboard", "other"}


def _spec(raw=None, **kw):
    return normalize(raw or PHOTO, max_windows=8, kinds=KINDS, apps=APPS, **kw)


def test_editor_kind_becomes_app_on_the_editor_base():
    spec = _spec({**PHOTO, "hero": "adjust"})
    assert spec.output == "app" and spec.base == "editor"
    assert spec.hero == ""  # 画布就是主体，没有主视觉窗口
    assert [w.id for w in spec.windows] == ["adjust", "crop", "background"]


def test_other_kinds_and_unknown_bases_stay_pages():
    assert _spec({**PHOTO, "kind": "dashboard"}).output == "page"
    assert _spec({**PHOTO, "kind": "没有这类"}).output == "page"
    odd = normalize(PHOTO, max_windows=8, kinds=KINDS, apps={"editor": "没有这个底座"})
    assert odd.output == "page" and odd.base == ""
    assert normalize(PHOTO, max_windows=8, kinds=KINDS).output == "page"  # 没开应用型


def test_app_shell_mounts_features_on_the_base():
    spec = _spec({**PHOTO, "title": "修图</script>"})
    html = shell_html(spec, "b-1")
    assert '<link rel="stylesheet" href="/kit/kit.css">' in html
    assert '<link rel="stylesheet" href="/kit/editor/editor.css">' in html
    assert '"/kit/editor/editor.js"' in html and "createEditor" in html
    assert "editor.addPanel" in html and "el.dataset.window = f.id" in html
    assert "editor.addTest" in html and "selftest" in html
    assert '</script>"' not in html and "修图<\\/script>" in html  # 标题不会提前结束脚本
    assert 'class="k-root k-app"' in html
    assert "%%" not in html
    assert "k-grid" not in html  # 不是卡片拼盘


def test_write_shell_writes_manifest_and_tests(tmp_path):
    spec = _spec()
    checks = {"undoMin": 20, "bigPx": 4000, "bigMaxMs": 8000}
    write_shell(tmp_path, spec, "b-1", app_checks=checks)
    app = json.loads((tmp_path / "app.json").read_text("utf-8"))
    assert app["base"] == "editor" and app["kind"] == "editor"
    assert [f["id"] for f in app["features"]] == ["adjust", "crop", "background"]
    assert app["features"][0]["file"] == "windows/adjust.js"
    assert app["checks"] == checks and app["sample"] == ""
    assert app["selftest"] == "index.html?selftest=1"
    assert "test(t)" in (tmp_path / "tests" / "README.md").read_text("utf-8")
    # 装了示例素材以后：第一张照片写进清单，门槛保留
    samples = [
        {"file": "assets/samples/models/fox.glb", "kind": "model"},
        {"file": "assets/samples/photos/coffee.png", "kind": "photo"},
    ]
    write_app_manifest(tmp_path, spec, samples=samples)
    app = json.loads((tmp_path / "app.json").read_text("utf-8"))
    assert app["sample"] == "assets/samples/photos/coffee.png" and app["checks"] == checks
    # 签字时重写外壳：应用型照样是底座，读回来的规格还是应用型
    assert load_spec(tmp_path).output == "app"
    assert "createEditor" in (tmp_path / "index.html").read_text("utf-8")


def test_page_output_has_no_app_manifest(tmp_path):
    write_shell(tmp_path, _spec({**PHOTO, "kind": "dashboard"}), "b-2")
    assert not (tmp_path / "app.json").exists()
    assert "k-grid" in (tmp_path / "index.html").read_text("utf-8")


def test_bases_are_registered_in_the_kit():
    manifest = json.loads((KIT_DIR / "manifest.json").read_text("utf-8"))
    names = {b["name"] for b in manifest["bases"]}
    assert names == set(BASES)
    for name, info in BASES.items():
        for key in ("js", "css", "selftest"):
            assert (KIT_DIR / info[key].removeprefix("/kit/")).is_file(), (name, key)
        api = base_api(name)
        for fn in ("addPanel", "addAction", "addTool", "applyFilter", "paint", "crop", "test(t)"):
            assert fn in api, fn
    js = (KIT_DIR / "editor" / "editor.js").read_text("utf-8")
    for fn in ("addPanel", "addAction", "addTool", "addTest", "applyFilter", "paint", "crop"):
        assert f"function {fn}(" in js, fn
    assert "export function createEditor" in js and "UNDO_MIN = 20" in js


def test_config_maps_editor_kind_and_check_names_exist():
    cfg = load_config(ROOT / "config").roundtable.bench
    assert cfg.apps.enabled and cfg.apps.bases == {"editor": "editor"}
    assert set(cfg.apps.bases) <= set(cfg.skills.kinds)
    assert set(cfg.apps.bases.values()) <= set(BASES)
    assert cfg.apps.undo_min >= 20 and cfg.apps.big_px >= 4000
    names = set(cfg.skills.checks["editor"])
    assert set(CHECK_NAMES.values()) | {FEATURE_FAIL} <= names


def test_findings_go_to_the_right_authors():
    results = [
        {"id": "k5-undo", "name": "撤销至少 20 步", "ok": False, "detail": "只记住了 3 步"},
        {"id": "k5-big", "name": "大图", "ok": True},
        {"id": "test:裁剪", "name": "裁剪", "ok": False, "detail": "尺寸没变", "feature": "crop"},
    ]
    got = findings_of(results, ["adjust", "crop"], errors=["窗口 crop 出错：boom"])
    by = {f.check: f for f in got}
    assert by["撤销步数不够"].windows == ["adjust", "crop"]  # 底座的问题交给所有功能
    assert "只记住了 3 步" in by["撤销步数不够"].detail
    assert by[FEATURE_FAIL].windows == ["crop"]  # 功能自带的测试只交给那个功能
    assert by["页面报错"].windows == ["crop"]
    assert all(f.severity == "block" for f in got)
    assert findings_of([{"id": "k5-keys", "ok": True}], ["a"]) == []


EDITOR_PLAN = {**PHOTO, "requirements": []}


class AppSC(FakeSC):
    """写功能时记下收到的提示词（看有没有底座的接口说明）。"""

    def __init__(self):
        super().__init__(plan=EDITOR_PLAN)
        self.window_code = (
            "export default (el, ctx) => { el.textContent = ctx.editor ? '好' : '坏'; };\n"
            "export async function test(t) { t.assert(true, ''); }"
        )


def fake_app_check(fail_rounds=1):
    calls = []

    def check(target, out_dir=None, *, allow=(), **kw):
        calls.append({"target": target, "out": str(out_dir)})
        out = Path(out_dir)
        shot = out / "应用_电脑.png"
        shot.write_bytes(b"png")
        bad = len(calls) <= fail_rounds
        results = [
            {"id": "k5-undo", "name": "撤销至少 20 步", "ok": True, "detail": ""},
            {
                "id": "test:裁剪",
                "name": "裁剪",
                "ok": not bad,
                "detail": "裁完尺寸没变" if bad else "",
                "feature": "crop",
            },
        ]
        r = AppReport(target=target, results=results, shots=[str(shot)])
        r.findings = findings_of(results, ["adjust", "crop", "background"])
        return r

    check.calls = calls
    return check


def _make(tmp_path, sc, app_check, **rules):
    b = make(
        tmp_path,
        sc,
        skills={"kinds": {"editor": {"label": "编辑器类", "skills": []}}},
        **rules,
    )
    b.app_check = app_check
    return b


async def test_editor_request_builds_an_app_and_runs_enterprise_checks(tmp_path):
    sc = AppSC()
    check = fake_app_check(fail_rounds=1)
    b = _make(tmp_path, sc, check)
    build = b.submit("修照片")
    await b.wait()
    assert build["status"] == "done", build["log"]
    assert build["output"] == {"kind": "app", "base": "editor"}
    folder = tmp_path / build["id"]
    assert "createEditor" in (folder / "index.html").read_text("utf-8")
    assert json.loads((folder / "app.json").read_text("utf-8"))["checks"]["undoMin"] == 20
    assert any("按应用型做：在编辑器底座上加 3 个功能" in x["text"] for x in build["log"])

    # 拆需求的助手知道哪些类型做成应用型、底座有什么；写功能的助手拿到底座接口
    plan = next(json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_plan")
    assert "editor：做成应用型" in plan["apps"] and "addPanel" in plan["apps"]
    windows = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_window"]
    assert len(windows) == 3
    assert all("这个台是应用型" in p["app"] and "applyFilter" in p["app"] for p in windows)
    assert all("撤销至少 20 步" in p["app"] for p in windows)

    # 第 1 轮「裁剪」自带的测试没过：不过线，只交回裁剪的作者；第 2 轮过线
    assert len(check.calls) == 2 and len(build["rounds"]) == 2
    r1, r2 = build["rounds"]
    assert not r1["passed"] and r1["app"]["ok"] == 1 and r1["app"]["total"] == 2
    assert any(f["check"] == FEATURE_FAIL for f in r1["check"]["findings"])
    assert "应用_电脑.png" in "".join(r1["check"]["shots"])
    reworks = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_rework"]
    got = {p["file"]: p for p in reworks}
    assert "裁完尺寸没变" in got["windows/crop.js"]["problems"]
    assert "这个台是应用型" in got["windows/crop.js"]["app"]
    others = [p for f, p in got.items() if f != "windows/crop.js"]
    assert all("裁完尺寸没变" not in p["problems"] for p in others)
    assert r2["passed"] and r2["app"]["ok"] == 2
    assert check.calls[0]["out"].endswith("round-1")


async def test_page_requests_skip_enterprise_checks(tmp_path):
    sc = FakeSC()  # 旅行台：不是编辑器类
    check = fake_app_check()
    b = _make(tmp_path, sc, check)
    build = b.submit("东京三天")
    await b.wait()
    assert build["status"] == "done"
    assert build["output"] == {"kind": "page", "base": ""}
    assert check.calls == [] and "app" not in build["rounds"][0]
    windows = [json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_window"]
    assert all("不是应用型" in p["app"] for p in windows)


async def test_apps_can_be_turned_off(tmp_path):
    sc = AppSC()
    check = fake_app_check()
    b = _make(tmp_path, sc, check, apps={"enabled": False})
    build = b.submit("修照片")
    await b.wait()
    assert build["output"]["kind"] == "page" and check.calls == []
    plan = next(json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_plan")
    assert plan["apps"] == "（这次都做成单页窗口）"


def test_finding_type_is_shared_with_quality():
    f = findings_of([{"id": "k5-error", "ok": False, "name": "出错有提示"}], ["a"])[0]
    assert isinstance(f, Finding) and f.check == "出错没有提示" and f.where == "企业级验收"


def test_features_on_the_editor_count_as_using_the_kit(tmp_path):
    """应用型的功能用 ctx.editor（底座是零件库的一部分），读代码检查不算「没用零件库」。"""
    from roundtable.core.benchkit.lint import lint_windows

    (tmp_path / "windows").mkdir()
    codes = {
        "a": "export default (el, ctx) => { ctx.editor.applyFilter('亮', 'brightness(1.2)'); };",
        "b": "export default async function mount(el, { editor }) { editor.crop(); }",
        "c": "export default (el, ctx) => { const { editor } = ctx; editor.undo(); };",
        "d": "export default (el) => { el.textContent = '什么都没用'; };",
    }
    for wid, code in codes.items():
        (tmp_path / "windows" / f"{wid}.js").write_text(code, encoding="utf-8")
    got = [f for f in lint_windows(tmp_path, list(codes)) if f.check == "窗口没用零件库"]
    assert len(got) == 1 and got[0].windows == ["d"]
