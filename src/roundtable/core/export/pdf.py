"""Markdown 块 → PDF。不加新依赖：手写 PDF 1.4。

中文用 PDF 阅读器自带的标准中文字体（STSong-Light，Adobe-GB1），不嵌入字体，
所以文件很小；Edge / Chrome / Acrobat / WPS 都能显示。
不在 GB 字符集里的字（如表情符号）可能显示为空白，
需要完全一致的版式时用「网页版」在浏览器里打印成 PDF。
"""

from __future__ import annotations

from roundtable.core.export.markdown import Block, parse, runs

PAGE_W, PAGE_H = 595.28, 841.89  # A4，单位磅
MARGIN = 56.0
BOTTOM = 60.0

_SIZES = {1: 20.0, 2: 16.0, 3: 13.5}


def _w(ch: str) -> float:
    """字宽（以字号为 1）：半角 0.5，其余 1。与字体字典里的 /W 一致。"""
    return 0.5 if " " <= ch <= "~" else 1.0


def _hex(text: str) -> str:
    return "<" + text.encode("utf-16-be").hex().upper() + ">"


def _tokens(text: str, bold: bool = False) -> list[tuple[str, bool]]:
    """拆成可以断行的小段：英文按词（带后面的空格），其余每个字一段。"""
    out: list[tuple[str, bool]] = []
    for r in runs(text) if text else []:
        b = bold or r.bold
        word = ""
        for ch in r.text.replace("\t", "    "):
            if " " < ch <= "~":
                word += ch
                continue
            if word:
                out.append((word, b))
                word = ""
            out.append((ch, b))
        if word:
            out.append((word, b))
    return out


def _wrap(
    tokens: list[tuple[str, bool]], width: float, size: float, keep: bool = False
) -> list[list[tuple[str, bool]]]:
    """按宽度断行；keep 为真时保留行首空格（代码缩进）。"""
    lines: list[list[tuple[str, bool]]] = [[]]
    used = 0.0
    for text, bold in tokens:
        w = sum(_w(c) for c in text) * size
        if lines[-1] and used + w > width and text != " ":
            lines.append([])
            used = 0.0
        if w > width:  # 超长的英文串（网址等）逐字断开
            for ch in text:
                cw = _w(ch) * size
                if lines[-1] and used + cw > width:
                    lines.append([])
                    used = 0.0
                lines[-1].append((ch, bold))
                used += cw
            continue
        if not lines[-1] and text == " " and not (keep and len(lines) == 1):
            continue
        lines[-1].append((text, bold))
        used += w
    return lines


class _Writer:
    def __init__(self) -> None:
        self.pages: list[list[str]] = []
        self.y = 0.0
        self._new_page()

    def _new_page(self) -> None:
        self.pages.append([])
        self.y = PAGE_H - MARGIN

    def space(self, pt: float) -> None:
        self.y -= pt

    def text(
        self,
        tokens,
        size: float,
        indent: float = 0.0,
        gray: float = 0.0,
        lead: float = 1.55,
        first: str = "",
        keep: bool = False,
    ) -> None:
        x = MARGIN + indent
        lines = _wrap(tokens, PAGE_W - MARGIN - x, size, keep) or [[]]
        for n, line in enumerate(lines):
            if self.y - size * lead < BOTTOM:
                self._new_page()
            self.y -= size * lead
            ops = [f"BT {gray:.2f} g {gray:.2f} G /F1 {size:.1f} Tf"]
            if n == 0 and first:
                start = x - sum(_w(c) for c in first) * size
                ops.append(f"1 0 0 1 {start:.2f} {self.y:.2f} Tm {_hex(first)} Tj")
            ops.append(f"1 0 0 1 {x:.2f} {self.y:.2f} Tm")
            bold = False
            for piece, b in line:
                if b != bold:
                    ops.append(f"{2 if b else 0} Tr {size * 0.03:.2f} w" if b else "0 Tr")
                    bold = b
                ops.append(f"{_hex(piece)} Tj")
            ops.append("0 Tr ET")
            self.pages[-1].append(" ".join(ops))

    def rule(self) -> None:
        if self.y - 12 < BOTTOM:
            self._new_page()
        self.y -= 8
        self.pages[-1].append(
            f"0.75 G 0.6 w {MARGIN:.2f} {self.y:.2f} m {PAGE_W - MARGIN:.2f} {self.y:.2f} l S"
        )
        self.y -= 4


def layout(md: str) -> list[list[str]]:
    """每页的绘图指令（测试也用它数页数）。"""
    w = _Writer()
    for b in parse(md):
        _block(w, b)
    return w.pages


def _block(w: _Writer, b: Block) -> None:
    if b.kind == "heading":
        size = _SIZES.get(b.level, 12.0)
        w.space(size * 0.6)
        w.text(_tokens(b.text, bold=True), size, lead=1.4)
        if b.level <= 2:
            w.rule()
        w.space(2)
    elif b.kind == "bullet":
        indent = 16.0 * (b.level + 1)
        mark = f"{b.number}. " if b.ordered else "• "
        w.text(_tokens(b.text), 11, indent=indent, first=mark)
    elif b.kind == "code":
        w.space(3)
        for line in b.text.split("\n"):
            w.text(_tokens(line), 9.5, indent=12, gray=0.25, lead=1.4, keep=True)
        w.space(4)
    elif b.kind == "table":
        w.space(3)
        for i, row in enumerate(b.rows):
            w.text(_tokens(" ｜ ".join(row), bold=(i == 0)), 10.5, indent=6, lead=1.5)
        w.space(4)
    elif b.kind == "quote":
        w.text(_tokens(b.text), 10.5, indent=14, gray=0.4)
        w.space(3)
    elif b.kind == "hr":
        w.rule()
    else:
        w.text(_tokens(b.text), 11)
        w.space(4)


def render(md: str, title: str = "") -> bytes:
    pages = layout(md)
    total = len(pages)
    objects: list[str] = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "",  # 页树，最后填
        "<< /Type /Font /Subtype /Type0 /BaseFont /STSong-Light /Encoding /UniGB-UTF16-H "
        "/DescendantFonts [4 0 R] >>",
        "<< /Type /Font /Subtype /CIDFontType0 /BaseFont /STSong-Light "
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (GB1) /Supplement 4 >> "
        "/FontDescriptor 5 0 R /DW 1000 /W [1 95 500 814 939 500] >>",
        "<< /Type /FontDescriptor /FontName /STSong-Light /Flags 6 /FontBBox [-25 -254 1000 880] "
        "/ItalicAngle 0 /Ascent 880 /Descent -120 /CapHeight 880 /StemV 93 >>",
        f"<< /Title {_hex_bom(title)} /Producer (Roundtable) >>",
    ]
    kids = []
    for n, ops in enumerate(pages, 1):
        footer = f"{n} / {total}"
        x = (PAGE_W - len(footer) * 0.5 * 9) / 2
        ops = [*ops, f"BT 0.55 g /F1 9 Tf 1 0 0 1 {x:.2f} 32 Tm {_hex(footer)} Tj ET"]
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            f"<< /Length {len(stream)} >>\nstream\n{stream.decode('latin-1')}\nendstream"
        )
        content_id = len(objects)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        )
        kids.append(f"{len(objects)} 0 R")
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {total} >>"
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def _hex_bom(text: str) -> str:
    return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"
