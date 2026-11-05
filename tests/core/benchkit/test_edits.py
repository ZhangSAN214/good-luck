"""生成器第二版 · 第 2 块：处处可改、存版本能退回、改了上游只重做受影响的窗口。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.benchkit.edits import EditError, check_key, diff_text
from tests.core.benchkit.test_builder import FakeSC, make


async def built(tmp_path):
    sc = FakeSC()
    bench = make(tmp_path, sc)
    b = bench.submit("帮我做个东京三天旅行的工作台")
    await bench.wait()
    assert b["status"] == "done"
    return sc, bench, b


def test_keys_are_checked():
    assert check_key("code:map-view") == ("code", "map-view")
    for bad in ("", "code:", "code:../x", "secrets", "window:A B"):
        with pytest.raises(EditError):
            check_key(bad)
    assert "+新" in diff_text("旧", "新")


async def test_code_versions_record_who_and_revert(tmp_path):
    sc, bench, b = await built(tmp_path)
    folder = tmp_path / b["id"]
    view = bench.edit_view(b["id"], "code:plan")
    assert view["versions"][0]["by"] in {"A", "B", "C", "a", "b", "c"}  # 助手写的那一版记了是谁
    first = view["value"]
    out = bench.edit(b["id"], "code:plan", first + "\n// 我加的一行", "加注释")
    assert out["changed"] and out["stale"] == {}  # 亲手改代码不用重做，只是评审过期
    assert b["review_stale"] == ["plan"]
    assert (folder / "windows" / "plan.js").read_text().endswith("// 我加的一行")
    vs = bench.edit_view(b["id"], "code:plan")["versions"]
    assert vs[-1]["by"] == "你" and vs[-1]["why"] == "加注释"
    d = bench.version_diff(b["id"], "code:plan", vs[-1]["n"])
    assert "+// 我加的一行" in d["diff"]
    bench.revert(b["id"], "code:plan", vs[-2]["n"])
    assert (folder / "windows" / "plan.js").read_text() == first
    assert bench.edit_view(b["id"], "code:plan")["versions"][-1]["why"].startswith("退回第")
    assert not bench.edit(b["id"], "code:plan", first)["changed"]  # 没变不存
    with pytest.raises(EditError, match="JSON"):
        bench.edit(b["id"], "data:plan", "{坏的")


async def test_window_spec_edit_marks_only_that_window_and_redo_fixes_it(tmp_path):
    sc, bench, b = await built(tmp_path)
    bench.edit(b["id"], "window:budget", {"purpose": "按人头算预算"}, "要按人头")
    assert list(b["stale"]) == ["budget"]
    assert "按人头算预算" in b["stale"]["budget"][0]["why"]
    spec = json.loads((tmp_path / b["id"] / "spec.json").read_text(encoding="utf-8"))
    assert next(w for w in spec["windows"] if w["id"] == "budget")["purpose"] == "按人头算预算"
    tree = bench.tree(b["id"])
    assert [s["window"] for s in tree["stale"]] == ["budget"]
    n = len(sc.calls)
    bench.redo_stale(b["id"])
    await bench.wait()
    redo = [c for c in sc.calls[n:] if c["role"] == "bench_rework"]
    assert len(redo) == 1  # 只重做受影响的那一个，别的窗口不动
    assert "按人头算预算" in sc.prompts[n]
    assert b["stale"] == {}
    with pytest.raises(ValueError, match="没有过期"):
        bench.redo_stale(b["id"])


async def test_stages_and_materials_mark_affected_windows(tmp_path):
    sc, bench, b = await built(tmp_path)
    b["proposal"] = {
        "milestones": [
            {"n": 1, "title": "最小可用", "goal": "能看", "windows": ["map-view"]},
            {"n": 2, "title": "主要功能", "goal": "能排", "windows": ["plan", "budget"]},
        ]
    }
    new = [
        {"title": "最小可用", "windows": ["map-view"]},
        {"title": "主要功能", "windows": ["plan"]},
        {"title": "算钱", "windows": ["budget"]},
    ]
    bench.edit(b["id"], "stages", new, "拆一段出来")
    assert set(b["stale"]) == {"plan", "budget"}  # 第 1 段没变，地图不动
    assert [m["title"] for m in b["proposal"]["milestones"]] == ["最小可用", "主要功能", "算钱"]
    b["stale"] = {}
    b["materials"] = [{"id": "m1", "file": "astronaut.jpg"}, {"id": "m2", "file": "x.png"}]
    bench.edit(b["id"], "materials", ["m2"], "这张不要")
    assert set(b["stale"]) == {w["id"] for w in b["windows"]}  # 每个窗口代码里都用到了这张照片
    assert [m["id"] for m in b["materials"]] == ["m2"]
    with pytest.raises(EditError, match="至少"):
        bench.edit(b["id"], "stages", [])


async def test_research_edits_reach_writers(tmp_path):
    sc, bench, b = await built(tmp_path)
    b["research"] = {"github": [{"repo": "x/y", "title": "x/y", "url": "https://github.com/x/y"}]}
    value = {
        "removed": [],
        "pinned": ["g1"],
        "added": [{"url": "https://example.org/a", "note": "好看"}],
    }
    bench.edit(b["id"], "research", value)
    assert set(b["stale"]) == {w["id"] for w in b["windows"]}
    text = bench._sources_text(b)
    assert "钉住了必须借鉴的调研条目：g1" in text and "https://example.org/a（好看）" in text
    with pytest.raises(EditError, match="链接不对"):
        bench.edit(b["id"], "research", {"added": [{"url": "javascript:alert(1)"}]})


async def test_instruct_sends_one_sentence_to_a_worker(tmp_path):
    sc, bench, b = await built(tmp_path)
    n = len(sc.calls)
    bench.instruct(b["id"], "map-view", "地图铺满整格", worker="b")
    await bench.wait()
    assert sc.calls[n]["role"] == "bench_rework" and sc.calls[n]["preset"] == "b"
    assert "你要这样改：地图铺满整格" in sc.prompts[n]
    with pytest.raises(ValueError, match="怎么改"):
        bench.instruct(b["id"], "map-view", " ")


async def test_answers_edit_marks_all_windows(tmp_path):
    sc, bench, b = await built(tmp_path)
    b["interview"] = {"questions": [{"id": "q1", "q": "给谁用？"}], "answers": {"q1": "自己"}}
    bench.edit(b["id"], "answers", {"q1": "爸妈"})
    assert set(b["stale"]) == {w["id"] for w in b["windows"]}
    assert "给谁用？ —— 爸妈" in b["stale"]["plan"][0]["why"]
