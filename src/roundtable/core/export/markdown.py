"""把成果的 Markdown 拆成块（标题 / 段落 / 列表 / 代码 / 表格 / 引用 / 分隔线），供各格式渲染。

只支持成员常写的那一小部分 Markdown；认不出的写法按普通文字保留，不丢内容。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 行内：**粗体**、*斜体*（或 _斜体_）、`代码`、[文字](链接)
_INLINE = re.compile(
    r"(\*\*(?P<b>.+?)\*\*|__(?P<b2>.+?)__|`(?P<c>[^`]+)`|\[(?P<lt>[^\]]+)\]\((?P<lu>[^)\s]+)\)"
    r"|(?<![\w*])\*(?P<i>[^*\s][^*]*?)\*(?![\w*]))"
)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET = re.compile(r"^(\s*)([-*+]|\d+[.)、])\s+(.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


@dataclass(frozen=True)
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False


@dataclass
class Block:
    kind: str  # heading / para / bullet / code / table / quote / hr
    text: str = ""
    level: int = 0  # 标题级别；列表缩进级别
    ordered: bool = False
    number: int = 0  # 有序列表的序号
    rows: list[list[str]] = field(default_factory=list)  # 表格（第一行是表头）


def runs(text: str) -> list[Run]:
    """行内格式拆成若干段；链接只留文字和网址。"""
    out: list[Run] = []
    pos = 0
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            out.append(Run(text[pos : m.start()]))
        if m.group("b") or m.group("b2"):
            out.append(Run(m.group("b") or m.group("b2"), bold=True))
        elif m.group("c"):
            out.append(Run(m.group("c"), code=True))
        elif m.group("lt"):
            label, url = m.group("lt"), m.group("lu")
            out.append(Run(label if label == url else f"{label}（{url}）"))
        else:
            out.append(Run(m.group("i"), italic=True))
        pos = m.end()
    if pos < len(text):
        out.append(Run(text[pos:]))
    return [r for r in out if r.text]


def plain(text: str) -> str:
    return "".join(r.text for r in runs(text))


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def parse(md: str) -> list[Block]:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append(Block("para", " ".join(s.strip() for s in para)))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            flush()
            fence = _FENCE.match(line).group(1)
            body: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                body.append(lines[i])
                i += 1
            blocks.append(Block("code", "\n".join(body)))
            i += 1
            continue
        if not line.strip():
            flush()
        elif m := _HEADING.match(line):
            flush()
            blocks.append(Block("heading", m.group(2), level=len(m.group(1))))
        elif _HR.match(line):
            flush()
            blocks.append(Block("hr"))
        elif "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush()
            rows = [_cells(line)]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(_cells(lines[i]))
                i += 1
            width = max(len(r) for r in rows)
            blocks.append(Block("table", rows=[r + [""] * (width - len(r)) for r in rows]))
            continue
        elif m := _BULLET.match(line):
            flush()
            mark = m.group(2)
            ordered = mark[0].isdigit()
            number = int(re.match(r"\d+", mark).group()) if ordered else 0
            level = len(m.group(1).replace("\t", "    ")) // 2
            blocks.append(
                Block("bullet", m.group(3), level=min(level, 3), ordered=ordered, number=number)
            )
        elif line.lstrip().startswith(">"):
            flush()
            text = line.lstrip()[1:].strip()
            if blocks and blocks[-1].kind == "quote" and text:
                blocks[-1].text += " " + text
            elif text:
                blocks.append(Block("quote", text))
        else:
            para.append(line)
        i += 1
    flush()
    return blocks


def title_and_sections(blocks: list[Block]) -> tuple[str, list[tuple[str, list[Block]]]]:
    """按一、二级标题分节（PPT 每节一页）：返回文档标题和 [(节标题, 内容块)]。"""
    title = ""
    sections: list[tuple[str, list[Block]]] = []
    for b in blocks:
        if b.kind == "heading" and b.level == 1 and not title and not sections:
            title = plain(b.text)
        elif b.kind == "heading" and b.level <= 2:
            sections.append((plain(b.text), []))
        else:
            if not sections:
                sections.append(("", []))
            sections[-1][1].append(b)
    return title, [(t, bs) for t, bs in sections if t or bs]
