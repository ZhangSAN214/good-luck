"""导出样式：字号、配色、PPT 比例在各格式里生效；不合法的值报错。"""

from __future__ import annotations

import io
import re
import zipfile

import pytest
from docx import Document as Docx

from roundtable.core.export import FORMATS, Style, options, pdf, pptx
from roundtable.core.export.deliverable import Document

MD = "# 标题\n\n## 一节\n\n" + "\n".join(f"- 第 {i} 条要点" for i in range(60))
LONG = "# 长\n\n" + "\n".join(f"- 第 {i} 条要点" for i in range(300))
DOC = Document("标题", "题目", MD)


def test_invalid_values_are_rejected():
    for bad in ({"size": "huge"}, {"theme": "pink"}, {"ratio": "1:1"}):
        with pytest.raises(ValueError):
            Style(**bad)
    opts = options()
    assert {t["value"] for t in opts["themes"]} >= {"blue", "serif"}
    assert [s["value"] for s in opts["sizes"]] == ["small", "medium", "large"]


def test_docx_uses_theme_font_color_and_size():
    d = Docx(io.BytesIO(FORMATS["docx"].render(DOC, Style(theme="serif", size="large"))))
    normal = d.styles["Normal"]
    assert normal.font.size.pt == 12.5 and normal.font.name == "Times New Roman"
    assert 'w:eastAsia="SimSun"' in normal.element.xml
    assert str(d.styles["Heading 1"].font.color.rgb) == "7B2D26"


def test_pptx_ratio_theme_and_size_change_the_layout():
    small = pptx.slides_of(MD, pptx._Deck.of(Style(size="small")))
    large = pptx.slides_of(MD, pptx._Deck.of(Style(size="large")))
    assert len(large) > len(small)  # 字大，一页放得少
    data = FORMATS["pptx"].render(DOC, Style(ratio="4:3", theme="green"))
    z = zipfile.ZipFile(io.BytesIO(data))
    assert 'cx="9144000" cy="6858000"' in z.read("ppt/presentation.xml").decode()
    assert "2E7D32" in z.read("ppt/slides/slide2.xml").decode()


def test_pdf_and_html_follow_size_and_accent():
    assert len(pdf.layout(MD * 3, Style(size="large"))) > len(
        pdf.layout(MD * 3, Style(size="small"))
    )
    assert b"0.180 0.490 0.196 rg" in FORMATS["pdf"].render(DOC, Style(theme="green"))
    page = FORMATS["html"].render(DOC, Style(theme="serif", size="small")).decode()
    assert re.search(r"--accent:#7B2D26;--font:[^;]*serif;--base:14.4px", page)
