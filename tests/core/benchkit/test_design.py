"""方案阶段的设计选型：目录、推荐的校验、版型排布、签字时写回规格和外壳。"""

from __future__ import annotations

import json

from roundtable.core.benchkit.spec import (
    DESIGN_DIMS,
    apply_design,
    design_brief,
    design_catalog,
    design_lines,
    design_of,
    layout_spans,
    normalize,
    shell_html,
)
from tests.core.benchkit.test_builder import PROJECT_PLAN, ProjectSC, project

THEMES = ["ink", "paper", "ocean"]


def spec_with(design=None, n=5):
    raw = {
        "title": "修图台",
        "theme": "ocean",
        "hero": "canvas",
        "windows": [{"id": "canvas", "title": "画布", "span": 8}]
        + [{"id": f"w{i}", "title": f"窗口{i}", "span": 4} for i in range(1, n)],
        "events": [{"name": "pick", "from": "w1", "to": ["canvas", "w2"]}],
        "requirements": [{"text": "能调色", "windows": ["w2", "canvas"]}],
    }
    if design is not None:
        raw["design"] = design
    return normalize(raw, max_windows=8)


def test_catalog_has_theme_after_layout_and_brief_lists_ids():
    keys = [d["key"] for d in design_catalog(THEMES)]
    assert keys == ["layout", "theme", "material", "density", "corner", "type"]
    theme = design_catalog(THEMES)[1]
    assert [o["id"] for o in theme["options"]] == THEMES and theme["options"][0]["label"] == "墨蓝"
    text = design_brief(THEMES)
    for _key, (_label, opts) in DESIGN_DIMS.items():
        for i, *_ in opts:
            assert i in text


def test_design_of_keeps_only_known_values():
    d = design_of(
        {
            "picks": {"layout": "workspace", "material": "黄金", "density": "compact"},
            "why": {"layout": "修图要左工具右属性" * 20, "material": ""},
            "refs": [
                {"name": "Photoshop", "borrow": "左工具栏"},
                {"name": "Photoshop", "borrow": "重复的"},
                {"name": "", "borrow": "没名字"},
                "乱写",
            ],
            "tagline": "修图，一步到位",
        },
        "ocean",
        THEMES,
    )
    assert d["picks"] == {
        "layout": "workspace",
        "theme": "ocean",
        "material": "solid",  # 认不得的回到默认
        "density": "compact",
        "corner": "normal",
        "type": "sans",
    }
    assert d["recommend"] == d["picks"]
    assert len(d["why"]["layout"]) == 120 and "material" not in d["why"]
    assert d["refs"] == [{"name": "Photoshop", "borrow": "左工具栏", "from": "方案"}]
    assert d["tagline"] == "修图，一步到位"
    assert design_of(None, "ink", THEMES)["picks"]["layout"] == "plan"


def test_normalize_keeps_design_and_spans_fill_rows():
    spec = spec_with({"picks": {"layout": "hero-side"}})
    assert spec.design["picks"]["layout"] == "hero-side"
    for lay, *_ in DESIGN_DIMS["layout"][1]:
        got = layout_spans(spec, lay)
        assert sorted(w for w, _ in got) == sorted(w.id for w in spec.windows)
        if lay != "plan":
            assert sum(n for _, n in got) % 12 == 0, lay
    assert layout_spans(spec, "hero-side")[:2] == [("canvas", 8), ("w1", 4)]
    assert layout_spans(spec, "workspace")[:3] == [("w1", 3), ("canvas", 6), ("w2", 3)]
    assert layout_spans(spec, "hero-wide")[0] == ("canvas", 12)
    assert {n for _, n in layout_spans(spec, "stream")} == {12}
    assert layout_spans(spec, "dashboard")[0] == ("canvas", 4)
    # 窗口太少就退回主视觉 + 侧栏
    small = spec_with(n=2)
    assert layout_spans(small, "workspace") == [("canvas", 8), ("w1", 4)]


def test_apply_design_writes_picks_order_spans_and_drops():
    spec = spec_with({"refs": [{"name": "Photoshop"}, {"name": "Figma"}]})
    drop = apply_design(
        spec,
        {
            "layout": "workspace",
            "theme": "paper",
            "material": "glass",
            "type": "没有这个",
            "refs": ["Figma"],
            "spans": {"w3": 12, "w4": 5, "canvas": "x"},
            "drop": ["w4", "canvas", "nope", "w4"],
        },
    )
    assert drop == ["w4"]  # 主视觉不能去、认不得的不管、重复的只算一次
    assert spec.theme == "paper"
    assert spec.design["picks"]["material"] == "glass" and spec.design["picks"]["type"] == "sans"
    assert spec.design["chosen"] is True
    assert [r["name"] for r in spec.design["refs"]] == ["Figma"]
    assert [(w.id, w.span) for w in spec.windows] == [
        ("w1", 3),
        ("canvas", 6),
        ("w2", 3),
        ("w3", 12),  # 你调的宽度盖过版型
    ]
    assert spec.events[0]["to"] == ["canvas", "w2"]
    assert spec.requirements[0]["windows"] == ["w2", "canvas"]
    lines = design_lines(spec)
    assert "版型：编辑器三栏（左边工具、中间画布、右边属性，像修图和设计软件）" in lines
    assert "配色：纸本（暖白纸感，适合阅读、手账、学习）" in lines
    assert lines[-1] == "参考：Figma"
    html = shell_html(spec, "x1")
    assert 'data-k-theme="paper" data-k-material="glass">' in html  # 默认值不写
    assert html.index('data-window="w1"') < html.index('data-window="canvas"')


def test_apply_design_keeps_at_least_two_windows():
    spec = spec_with(n=3)
    assert apply_design(spec, {"drop": ["w1", "w2"]}) == ["w1"]
    assert [w.id for w in spec.windows] == ["canvas", "w2"]


async def test_sign_with_design_rewrites_spec_shell_and_brief(tmp_path):
    sc = ProjectSC()
    sc.plan = PROJECT_PLAN | {
        "design": {
            "picks": {"layout": "hero-side", "material": "paper"},
            "why": {"layout": "边看地图边改行程"},
            "refs": [{"name": "Google 旅行", "borrow": "地图和行程并排"}],
        }
    }
    b = project(tmp_path, sc)
    b.submit("旅行台")
    await b.wait()
    bid = b.build["id"]
    b.answer(bid, {})
    await b.wait()
    plan_prompt = next(json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_plan")
    assert "workspace" in plan_prompt["design"]
    d = b.build["proposal"]["design"]
    assert [x["key"] for x in d["dims"]][:2] == ["layout", "theme"]
    assert d["recommend"]["layout"] == "hero-side" and d["why"]["layout"] == "边看地图边改行程"
    assert d["refs"][0] == {"name": "Google 旅行", "borrow": "地图和行程并排", "from": "方案"}
    prop = b.build["proposal"]
    assert prop["windows"][0]["parts"] == ["mapFrame"] and "actions" in prop["windows"][0]
    book = (tmp_path / bid / "方案书.md").read_text(encoding="utf-8")
    assert "## 设计选型" in book and "版型：主视觉 + 侧栏" in book and "### 地图（主视觉）" in book

    b.sign(
        bid,
        design={
            "layout": "hero-wide",
            "theme": "forest",
            "density": "compact",
            "refs": [],
            "spans": {"budget": 12},
            "drop": ["plan"],
        },
    )
    await b.wait()
    spec = json.loads((tmp_path / bid / "spec.json").read_text(encoding="utf-8"))
    assert spec["theme"] == "forest" and spec["design"]["picks"]["density"] == "compact"
    assert spec["design"]["refs"] == []
    assert [(w["id"], w["span"]) for w in spec["windows"]] == [("map-view", 12), ("budget", 12)]
    html = (tmp_path / bid / "index.html").read_text(encoding="utf-8")
    assert 'data-k-theme="forest"' in html and 'data-k-density="compact"' in html
    assert 'data-k-material="paper"' in html  # 没改的保留推荐
    assert [w["id"] for w in b.build["windows"]] == ["map-view", "budget"]
    assert [m["windows"] for m in b.build["proposal"]["milestones"]] == [["map-view"], ["budget"]]
    assert "设计选型（你在选型台上定的）" in b.build["brief"] and "主视觉通栏" in b.build["brief"]
    assert any(t["title"] == "你定了设计" for t in b.build["timeline"])
