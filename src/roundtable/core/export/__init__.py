"""导出成果：Markdown / Word / PPT / PDF / 网页。

成果只由模型写一次（存库的 Markdown），各格式都由本地代码转换，导出不调用模型、不花 token，
也不加新依赖（Word 用已有的 python-docx，PPT 和 PDF 直接写文件格式）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from roundtable.core.export import docx, html, pdf, pptx
from roundtable.core.export.deliverable import Document, deliverable


@dataclass(frozen=True)
class Format:
    ext: str
    mime: str
    label: str
    render: Callable[[Document], bytes]


FORMATS: dict[str, Format] = {
    "md": Format(
        "md", "text/markdown; charset=utf-8", "Markdown", lambda d: d.markdown.encode("utf-8")
    ),
    "docx": Format(
        "docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Word",
        lambda d: docx.render(d.markdown),
    ),
    "pptx": Format(
        "pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "PPT",
        lambda d: pptx.render(d.markdown),
    ),
    "pdf": Format("pdf", "application/pdf", "PDF", lambda d: pdf.render(d.markdown, d.title)),
    "html": Format(
        "html", "text/html; charset=utf-8", "网页", lambda d: html.render(d.markdown, d.title)
    ),
}


def filename(doc: Document, session_id: str, ext: str) -> str:
    """圆桌-<标题前 20 字>-<会话 id 前 8 位>.<扩展名>；去掉文件名里不能用的字符。"""
    bad = '\\/:*?"<>|\r\n\t'
    stem = "".join("_" if c in bad else c for c in doc.title).strip(" ._…")[:20] or "成果"
    return f"圆桌-{stem}-{session_id[:8]}.{ext}"


__all__ = ["FORMATS", "Document", "Format", "deliverable", "filename"]
