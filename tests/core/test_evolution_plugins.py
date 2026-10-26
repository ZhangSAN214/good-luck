"""革新例会·插件清单：解析校验（缺字段不要、场景兜底、截断、去重）、Markdown、存取。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from roundtable.core.config.schema import EvolutionPluginRules
from roundtable.core.evolution_plugins import (
    Plugin,
    by_scene,
    cards_text,
    dump_lists,
    ideas_text,
    load_lists,
    merge,
    parse_plugins,
    plugins_from,
    plugins_markdown,
)
from roundtable.core.ideaboard import Board, Ranked

SCENES = ["办公室工作", "群聊", "通用"]


def item(name, scene="群聊", **kw):
    base = {
        "name": name,
        "scene": scene,
        "what": "做什么",
        "use": "有什么用",
        "pros": "优点",
        "cons": "缺点",
        "why": "为什么",
        "source": "资料卡 1",
    }
    return {**base, **kw}


def test_parse_keeps_complete_items_and_falls_back_scene():
    text = json.dumps(
        {
            "plugins": [
                item("A"),
                item("B", scene="火星"),  # 不在清单里：归到最后一个场景
                item("C", why=""),  # 缺字段：不要
                item("D", source=""),  # 没写参考：算成员补充
                "不是对象",
                item("E", what="字" * 500),
            ]
        }
    )
    got = parse_plugins(text, SCENES, field_chars=50)
    assert [p.name for p in got] == ["A", "B", "D", "E"]
    assert got[1].scene == "通用" and got[2].source == "成员补充"
    assert len(got[3].what) == 50 and got[3].what.endswith("…")


def test_parse_bad_json():
    assert parse_plugins("不是 JSON", SCENES, field_chars=50) == []
    assert parse_plugins('{"plugins": "x"}', SCENES, field_chars=50) == []


def test_merge_dedups_by_name():
    def parse(*items):
        return parse_plugins(json.dumps({"plugins": list(items)}), SCENES, field_chars=50)

    a, b = parse(item("记忆 衰减")), parse(item("记忆衰减"), item("新的"))
    assert [p.name for p in merge(a, b)] == ["记忆 衰减", "新的"]


def test_ideas_text_hides_authors_and_cards_are_numbered():
    board = Board((Ranked("I1", "朋友圈去重", ("愚者",), "idea", (), 9, 2, 3.0),))
    text = ideas_text(board)
    assert "I1" in text and "朋友圈去重" in text and "愚者" not in text
    assert ideas_text(Board()) == "（本场没有点子）"
    assert cards_text([("a.md", "正文")]).startswith("## 资料卡 1（a.md）")


def test_markdown_groups_by_scene_and_storage_roundtrip():
    plugins = [Plugin(**item("A", "群聊")), Plugin(**item("B", "办公室工作"))]
    text = plugins_markdown(
        week="2026-W44", session_id="s", plugins=plugins, cost_usd=0.01, scenes=SCENES
    )
    assert text.index("## 办公室工作") < text.index("## 群聊")
    assert "### 1. B" in text and "**缺点**：缺点" in text and "**为什么做**" in text
    assert by_scene(plugins, SCENES) == {"办公室工作": 1, "群聊": 1}
    raw = dump_lists({"s": {"plugins": [item("A")], "at": "1"}})
    assert [p.name for p in plugins_from(load_lists(raw)["s"])] == ["A"]
    assert load_lists("坏") == {}


def test_rules_validation():
    assert EvolutionPluginRules().count >= 20
    with pytest.raises(ValidationError):
        EvolutionPluginRules(scenes=["a", "a"])
    with pytest.raises(ValidationError):
        EvolutionPluginRules(dir="../x")
