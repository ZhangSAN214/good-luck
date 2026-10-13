"""思维导图渲染：Markdown / Mermaid / SVG，文字转义，数据往返。"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from roundtable.core.mindmap import Branch, MindMap, Node, to_markdown, to_mermaid, to_svg


def sample() -> MindMap:
    return MindMap(
        "降低淬火开裂",
        (
            Branch(
                "工艺",
                (Node("改用油淬 (慢冷)", ("愚者",)), Node("先去应力再回火", ("魔术师", "女祭司"))),
            ),
            Branch("材料", (Node("选低碳合金钢"),)),
            Branch("空分支"),
        ),
        ("先做小试样验证",),
    )  # fmt: skip


def test_dict_round_trip_and_counts():
    m = sample()
    assert MindMap.from_dict(m.to_dict()) == m and m.idea_count() == 3


def test_markdown_outline_lists_sources_and_next_steps():
    text = to_markdown(sample())
    assert text.startswith("# 降低淬火开裂")
    assert "- 改用油淬 (慢冷)（愚者）" in text and "（魔术师、女祭司）" in text
    assert "## 下一步\n1. 先做小试样验证" in text


def test_mermaid_strips_syntax_breaking_characters_and_keeps_structure():
    m = MindMap("主题(含括号)", (Branch("分[支]", (Node('想法: "引号" {花括号}\n换行'),)),))
    lines = to_mermaid(m).splitlines()
    assert lines[0] == "mindmap" and lines[1].startswith("  root((")
    body = "\n".join(lines[1:])
    assert not re.search(r"[\[\]{}\"]", body.replace("root((", "").replace("))", ""))
    assert all("\n" not in x for x in lines)
    assert lines[2].startswith("    ") and lines[3].startswith("      ")


def test_svg_is_valid_xml_escapes_text_and_has_no_script():
    m = MindMap("<script>alert(1)</script>", (Branch("a&b", (Node("</text><b>x</b>"),)),))
    svg = to_svg(m)
    root = ET.fromstring(svg)  # 合法 XML
    assert root.tag.endswith("svg")
    assert "<script" not in svg and "</text><b>" not in svg
    assert "&lt;script&gt;" in svg and "a&amp;b" in svg


def test_svg_grows_with_content_and_wraps_long_text():
    short = to_svg(MindMap("x", (Branch("a", (Node("短"),)),)))
    many = MindMap("x", tuple(Branch(f"分支{i}", (Node("很长的想法" * 20),) * 3) for i in range(4)))
    long = to_svg(many)
    h = lambda s: float(re.search(r'height="([\d.]+)"', s).group(1))  # noqa: E731
    assert h(long) > h(short) * 3
    assert "…" in long  # 超过三行的截断
    assert to_svg(MindMap("无分支", ()))  # 空导图不崩溃
