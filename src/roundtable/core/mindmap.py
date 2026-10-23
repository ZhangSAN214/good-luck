"""思维导图（R16）：头脑风暴整合的结果。同一份数据渲染成 Markdown 大纲、Mermaid mindmap 和 SVG 图。

纯数据与字符串处理，不调用模型，也不依赖任何模型或厂商。SVG 只用基本图形，文字全部转义。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape


@dataclass(frozen=True)
class Node:
    text: str
    sources: tuple[str, ...] = ()  # 提出或补充这个想法的成员（称呼）
    refs: tuple[str, ...] = ()  # 来源的点子编号（I3、E1……），网页据此关联投票和杂交


@dataclass(frozen=True)
class Branch:
    name: str
    ideas: tuple[Node, ...] = ()


@dataclass(frozen=True)
class MindMap:
    title: str
    branches: tuple[Branch, ...]
    next_steps: tuple[str, ...] = ()
    degraded: bool = False  # 统筹的整合结果不可用，由代码按成员分组

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "branches": [
                {
                    "name": b.name,
                    "ideas": [
                        {"text": n.text, "sources": list(n.sources), "refs": list(n.refs)}
                        for n in b.ideas
                    ],
                }
                for b in self.branches
            ],
            "next_steps": list(self.next_steps),
            "degraded": self.degraded,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MindMap:
        return cls(
            d["title"],
            tuple(
                Branch(
                    b["name"],
                    tuple(
                        Node(n["text"], tuple(n.get("sources", ())), tuple(n.get("refs", ())))
                        for n in b["ideas"]
                    ),
                )
                for b in d["branches"]
            ),
            tuple(d.get("next_steps", ())),
            bool(d.get("degraded", False)),
        )

    def idea_count(self) -> int:
        return sum(len(b.ideas) for b in self.branches)


# --- Markdown ---------------------------------------------------------------------


def to_markdown(m: MindMap) -> str:
    lines = [f"# {m.title}", ""]
    for b in m.branches:
        lines.append(f"## {b.name}")
        for n in b.ideas:
            by = f"（{'、'.join(n.sources)}）" if n.sources else ""
            lines.append(f"- {n.text}{by}")
        lines.append("")
    if m.next_steps:
        lines.append("## 下一步")
        lines.extend(f"{i}. {s}" for i, s in enumerate(m.next_steps, 1))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- Mermaid ----------------------------------------------------------------------

_MERMAID_BAD = re.compile(r"[()\[\]{}\"'`#;:<>|\\]")


def _mm(text: str, limit: int = 60) -> str:
    """Mermaid 节点文字：去掉会破坏语法的符号和换行。"""
    clean = _MERMAID_BAD.sub(" ", " ".join(text.split())).strip() or "·"
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def to_mermaid(m: MindMap) -> str:
    lines = ["mindmap", f"  root(({_mm(m.title, 30)}))"]
    for b in m.branches:
        lines.append(f"    {_mm(b.name, 30)}")
        lines.extend(f"      {_mm(n.text)}" for n in b.ideas)
    if m.next_steps:
        lines.append("    下一步")
        lines.extend(f"      {_mm(s)}" for s in m.next_steps)
    return "\n".join(lines) + "\n"


# --- SVG --------------------------------------------------------------------------

PALETTE = (
    ("#e8f0fb", "#3a6fb0"),
    ("#e9f5ee", "#2f8a5a"),
    ("#fdf0e0", "#c07a1c"),
    ("#f6e9f3", "#a1478c"),
    ("#e6f4f4", "#2a8a8c"),
    ("#fbebe9", "#c0503f"),
)
ROOT_FILL, ROOT_STROKE = "#2f3b45", "#2f3b45"
LINE_H, PAD_Y, GAP = 16, 8, 10


def _width(ch: str) -> float:
    return 14.0 if unicodedata.east_asian_width(ch) in "WF" else 7.5


def _wrap(text: str, max_w: float, max_lines: int) -> list[str]:
    lines, cur, w = [], "", 0.0
    for ch in " ".join(text.split()):
        if w + _width(ch) > max_w and cur:
            lines.append(cur)
            cur, w = "", 0.0
        cur += ch
        w += _width(ch)
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def _box(x, y, w, lines, fill, stroke, color, bold=False) -> tuple[str, float]:
    h = len(lines) * LINE_H + 2 * PAD_Y
    weight = ' font-weight="bold"' if bold else ""
    out = [
        f'<rect x="{x}" y="{y - h / 2:.1f}" width="{w}" height="{h}" rx="8" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>'
    ]
    for i, line in enumerate(lines):
        ty = y - h / 2 + PAD_Y + LINE_H * (i + 0.78)
        out.append(
            f'<text x="{x + 10}" y="{ty:.1f}" fill="{color}" font-size="13"{weight}>'
            f"{escape(line)}</text>"
        )
    return "".join(out), h


def to_svg(m: MindMap) -> str:
    """横向树图：中心主题 → 分支 → 想法。自带白底，放进 <img> 里在深浅色主题下都清晰。"""
    root_w, branch_w, idea_w = 150, 170, 300
    x_root, x_branch, x_idea = 16, 16 + root_w + 60, 16 + root_w + 60 + branch_w + 50
    cursor = 16.0
    placed = []  # (branch, branch_y, [(idea_lines, idea_y)])
    for b in m.branches:
        ideas = []
        start = cursor
        for n in b.ideas:
            text = n.text + (f"（{'、'.join(n.sources)}）" if n.sources else "")
            lines = _wrap(text, idea_w - 20, 3)
            h = len(lines) * LINE_H + 2 * PAD_Y
            ideas.append((lines, cursor + h / 2))
            cursor += h + GAP
        if not ideas:
            cursor += LINE_H + 2 * PAD_Y + GAP
        branch_y = (ideas[0][1] + ideas[-1][1]) / 2 if ideas else start + 20
        placed.append((b, branch_y, ideas))
        cursor += GAP
    height = max(cursor + 8, 80)
    root_y = sum(p[1] for p in placed) / len(placed) if placed else height / 2
    width = x_idea + idea_w + 16
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height:.0f}" '
        f'width="{width}" height="{height:.0f}" font-family="system-ui, \'PingFang SC\', '
        f"'Microsoft YaHei', sans-serif\">",
        f'<rect width="{width}" height="{height:.0f}" fill="#ffffff"/>',
    ]
    links, boxes = [], []
    root_lines = _wrap(m.title, root_w - 20, 4)
    svg, _ = _box(x_root, root_y, root_w, root_lines, ROOT_FILL, ROOT_STROKE, "#ffffff", True)
    boxes.append(svg)
    for i, (b, by, ideas) in enumerate(placed):
        fill, stroke = PALETTE[i % len(PALETTE)]
        x0, x1 = x_root + root_w, x_branch
        links.append(_curve(x0, root_y, x1, by, stroke))
        svg, _ = _box(
            x_branch, by, branch_w, _wrap(b.name, branch_w - 20, 2), fill, stroke, "#1f2528", True
        )
        boxes.append(svg)
        for lines, iy in ideas:
            links.append(_curve(x_branch + branch_w, by, x_idea, iy, stroke))
            svg, _ = _box(x_idea, iy, idea_w, lines, "#ffffff", stroke, "#1f2528")
            boxes.append(svg)
    parts.extend(links + boxes)
    parts.append("</svg>")
    return "".join(parts)


def _curve(x0: float, y0: float, x1: float, y1: float, color: str) -> str:
    mid = (x0 + x1) / 2
    return (
        f'<path d="M{x0:.1f},{y0:.1f} C{mid:.1f},{y0:.1f} {mid:.1f},{y1:.1f} {x1:.1f},{y1:.1f}" '
        f'fill="none" stroke="{color}" stroke-width="1.6"/>'
    )
