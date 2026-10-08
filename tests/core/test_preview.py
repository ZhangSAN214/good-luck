from __future__ import annotations

import io

from roundtable.core.preview import render_preview


def test_text_code_and_markdown():
    assert render_preview("a.md", "# 标题".encode())["format"] == "markdown"
    code = render_preview("x.py", b"print(1)")
    assert code["format"] == "code" and code["language"] == "py" and code["text"] == "print(1)"
    html = render_preview("page.html", b"<script>alert(1)</script>")
    assert html["type"] == "text" and html["format"] == "code"  # 只作为源代码预览
    long = render_preview("big.txt", b"x" * 50000)
    assert long["truncated"] and len(long["text"]) == 20000


def test_tables_and_documents():
    csv = render_preview("t.csv", b"a,b\n1,2\n")
    assert csv["type"] == "table" and csv["sheets"][0]["rows"] == [["a", "b"], ["1", "2"]]

    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["x", 1])
    buf = io.BytesIO()
    wb.save(buf)
    xlsx = render_preview("t.xlsx", buf.getvalue())
    assert xlsx["sheets"][0]["rows"] == [["x", "1"]]

    from .attachments.samples import docx, pdf

    assert "段落" in render_preview("d.docx", docx("段落"))["text"]
    assert "Hello pdf" in render_preview("p.pdf", pdf("Hello pdf"))["text"]
    assert render_preview("x.png", b"")["type"] == "image"
    assert render_preview("x.pptx", b"")["type"] == "none"
