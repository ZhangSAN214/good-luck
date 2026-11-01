"""方案阶段的设计选型：目录、推荐的校验、版型排布、签字时写回规格和外壳。"""

from __future__ import annotations

import json

from roundtable.core.benchkit.spec import (
    DESIGN_DIMS,
    apply_design,
    assets_of,
    design_brief,
    design_catalog,
    design_lines,
    design_of,
    layout_spans,
    look_of,
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


def test_catalog_groups_theme_and_accent_and_brief_lists_ids():
    cat = design_catalog(THEMES)
    keys = [d["key"] for d in cat]
    assert keys[:4] == ["layout", "width", "topbar", "herosize"]
    assert keys[4:7] == ["theme", "accent", "bg"]
    assert len(keys) == len(DESIGN_DIMS) + 2 and len(keys) >= 17
    groups = [d["group"] for d in cat]
    assert groups == sorted(groups, key=["结构", "色彩", "质感", "文字", "控件"].index)
    theme = cat[4]
    assert [o["id"] for o in theme["options"]] == THEMES and theme["options"][0]["label"] == "墨蓝"
    assert cat[5]["kind"] == "color" and cat[5]["options"] == []
    text = design_brief(THEMES)
    assert "accent（主色）：写 #rrggbb" in text
    for _key, (_label, _group, opts) in DESIGN_DIMS.items():
        for i, *_ in opts:
            assert i in text


def test_design_of_keeps_only_known_values():
    d = design_of(
        {
            "picks": {
                "layout": "workspace",
                "material": "黄金",
                "density": "compact",
                "accent": "#FF8800",
                "button": "pill",
            },
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
    picks = d["picks"]
    assert picks["layout"] == "workspace" and picks["theme"] == "ocean"
    assert picks["material"] == "solid"  # 认不得的回到默认
    assert picks["density"] == "compact" and picks["button"] == "pill"
    assert picks["accent"] == "#ff8800" and picks["corner"] == "normal"
    assert design_of({"picks": {"accent": "red"}}, "ink", THEMES)["picks"]["accent"] == ""
    assert d["recommend"] == d["picks"]
    assert len(d["why"]["layout"]) == 120 and "material" not in d["why"]
    assert d["refs"] == [{"name": "Photoshop", "borrow": "左工具栏", "from": "方案"}]
    assert d["tagline"] == "修图，一步到位"
    assert design_of(None, "ink", THEMES)["picks"]["layout"] == "plan"


def test_normalize_keeps_design_and_spans_fill_rows():
    spec = spec_with({"picks": {"layout": "hero-side"}})
    assert spec.design["picks"]["layout"] == "hero-side"
    for lay, *_ in DESIGN_DIMS["layout"][2]:
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
    assert [x["key"] for x in d["dims"]][:2] == ["layout", "width"]
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


def test_shell_writes_every_style_accent_note_and_assets():
    spec = spec_with()
    mats = [
        {"id": "a1", "name": "标.png", "kind": "image", "file": "a1.png", "of": ""},
        {"id": "a2", "name": "海.jpg", "kind": "image", "file": "a2.jpg", "of": ""},
        {"id": "v1", "name": "片.mp4", "kind": "video", "file": "v1.mp4", "of": ""},
        {"id": "f1", "name": "帧", "kind": "frame", "file": "f1.jpg", "of": "v1"},
    ]
    apply_design(
        spec,
        {
            "bg": "image",
            "button": "pill",
            "tone": "lively",
            "accent": "#FFD400",
            "note": "像胶片相机" * 60,
            "assets": {"a1": "logo", "a2": "bg", "v1": "hero", "f1": "gallery"},
        },
        mats,
    )
    d = spec.design
    assert d["picks"]["accent"] == "#ffd400" and len(d["note"]) == 200
    assert [(a["id"], a["role"], a["file"]) for a in d["assets"]] == [
        ("a1", "logo", "assets/a1.png"),
        ("a2", "bg", "assets/a2.jpg"),
        ("v1", "hero", "assets/v1.mp4"),  # 截帧不单独放进成品
    ]
    html = shell_html(spec, "x")
    assert 'data-k-bg="image"' in html and 'data-k-button="pill"' in html
    assert "data-k-tone" not in html and "data-k-layout" not in html  # 只影响文案的不写
    assert "--k-accent: #ffd400; --k-accent-fg: #111418" in html  # 亮黄上用深色字
    assert "--k-bg-image: url(&#x27;assets/a2.jpg&#x27;)" in html
    assert '<img src="assets/a1.png" alt="标志">' in html
    lines = design_lines(spec)
    assert "主色：#ffd400（盖过配色里的主色）" in lines
    assert any(t.startswith("你的风格要求：像胶片相机") for t in lines)
    assert "素材「片.mp4」当主视觉图，文件 assets/v1.mp4" in lines
    assert "文案语气：活泼（带点俏皮，适合游戏和娱乐）" in lines
    # 背景选了素材图但没有标背景的图：退回柔光；视频不能当 Logo 和背景；单个用处只留最后一个
    apply_design(spec, {"assets": {"v1": "bg", "a1": "hero", "a2": "hero"}}, mats)
    assert [(a["id"], a["role"]) for a in spec.design["assets"]] == [("a2", "hero")]
    assert "data-k-bg" not in shell_html(spec, "x")
    assert assets_of(mats, {"a1": "nope"}) == []


def test_look_of_keeps_known_files_and_hex_colors():
    got = look_of(
        {
            "items": [
                {"file": "materials/a.png", "what": "修图软件截图", "palette": ["#112233", "蓝"]},
                {"file": "a.png", "what": "重复"},
                {"file": "nope.png", "what": "没给它看的"},
                {
                    "file": "b.jpg",
                    "style": ["极简", "", "复古风格非常长的关键词"],
                    "borrow": "暖橙",
                },
            ],
            "summary": "暖色、极简",
            "palette": ["#FFAA00", "#112233", "bad"],
        },
        ["a.png", "b.jpg"],
    )
    assert [x["file"] for x in got["items"]] == ["a.png", "b.jpg"]
    assert got["items"][0]["palette"] == ["#112233"]
    assert got["items"][1]["style"] == ["极简", "复古风格非常长的关键词"]
    assert got["palette"] == ["#ffaa00", "#112233"] and got["summary"] == "暖色、极简"
