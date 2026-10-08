"""成员生成的文件的预览：文本、代码、CSV、Markdown 直接给文字；xlsx 给前几行；docx / pdf 给文字。

只读取内容，不执行任何东西；HTML 与 SVG 作为文本（源代码）预览，页面里不渲染。
"""

from __future__ import annotations

import csv
import io
from typing import Any

TEXT_LIMIT = 20000
MAX_ROWS, MAX_COLS = 50, 20


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _clip(text: str) -> tuple[str, bool]:
    return (text[:TEXT_LIMIT], True) if len(text) > TEXT_LIMIT else (text, False)


def render_preview(path: str, data: bytes) -> dict[str, Any]:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("png", "jpg", "jpeg", "gif", "webp"):
        return {"type": "image"}
    if ext == "csv":
        rows = list(csv.reader(io.StringIO(_text(data))))
        return {
            "type": "table",
            "sheets": [{"name": path, "rows": [r[:MAX_COLS] for r in rows[:MAX_ROWS]]}],
            "truncated": len(rows) > MAX_ROWS,
        }
    if ext == "xlsx":
        import openpyxl

        book = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheets = []
        for ws in book.worksheets[:5]:
            rows = []
            for row in ws.iter_rows(max_row=MAX_ROWS, max_col=MAX_COLS, values_only=True):
                cells = ["" if v is None else str(v) for v in row]
                while cells and cells[-1] == "":
                    cells.pop()
                rows.append(cells)
            while rows and not rows[-1]:
                rows.pop()
            sheets.append({"name": ws.title, "rows": rows})
        return {"type": "table", "sheets": sheets, "truncated": False}
    if ext == "docx":
        import docx

        document = docx.Document(io.BytesIO(data))
        text, cut = _clip("\n".join(p.text for p in document.paragraphs))
        return {"type": "text", "format": "plain", "text": text, "truncated": cut}
    if ext == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "") for p in reader.pages[:20]]
        text, cut = _clip("\n\n".join(pages))
        return {"type": "text", "format": "plain", "text": text, "truncated": cut}
    if ext in ("txt", "md", "py", "json", "tex", "html", "svg"):
        text, cut = _clip(_text(data))
        fmt = {"md": "markdown", "txt": "plain"}.get(ext, "code")
        return {"type": "text", "format": fmt, "language": ext, "text": text, "truncated": cut}
    return {"type": "none", "reason": "这种文件不能预览，请下载查看"}
