"""从一场的记录里取出最终成果，整理成一份 Markdown（导出 Word / PPT / PDF 的共同来源）。

只读已经存库的产出，不调用模型：导出不花任何 token。取的是交付给用户的那一版
（办公室的交付 / 修改后版本、协同的合并成果、讨论的最终答案、头脑风暴的点子榜与大纲、
单人作答的答案），过程（各人初稿、互评）不放进去。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from roundtable.core.storage.repository import OutputView, SessionView

_HEADING = re.compile(r"^(#{1,6})(\s)")
_FENCE = re.compile(r"^\s*(```|~~~)")


@dataclass(frozen=True)
class Document:
    title: str
    question: str
    markdown: str


def _load(o: OutputView) -> dict[str, Any]:
    try:
        data = json.loads(o.content)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def shift_headings(md: str, top: int) -> str:
    """把正文里的标题整体平移，使最高一级变成 top 级（代码块里的 # 不动）。"""
    lines = md.split("\n")
    levels, fenced = [], False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (m := _HEADING.match(line)):
            levels.append(len(m.group(1)))
    if not levels:
        return md
    delta = top - min(levels)
    out, fenced = [], False
    for line in lines:
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and (m := _HEADING.match(line)):
            level = max(1, min(6, len(m.group(1)) + delta))
            line = "#" * level + line[len(m.group(1)) :]
        out.append(line)
    return "\n".join(out)


def _items(title: str, items: list[Any] | None) -> str:
    items = [str(x).strip() for x in items or [] if str(x).strip()]
    if not items:
        return ""
    return f"## {title}\n\n" + "\n".join(f"- {x}" for x in items)


def _table(outputs: list[OutputView]) -> tuple[str, list[str]]:
    """一张桌子的成果正文和附加小节（仍需核实等）。没有成果时正文为空。"""
    by_kind: dict[str, OutputView] = {}
    for o in outputs:
        by_kind.setdefault(o.kind, o)
    extras: list[str] = []
    for kind in ("office_revised", "office_decision", "office_fix", "office_work"):
        if kind in by_kind and (text := _load(by_kind[kind]).get("text")):
            return text, extras
    if "merge" in by_kind:
        m = _load(by_kind["merge"])
        extras += [_items("缺失", m.get("gaps")), _items("仍需核实", m.get("open_questions"))]
        return m.get("result") or "", extras
    if "synthesis" in by_kind:
        s = _load(by_kind["synthesis"])
        extras.append(_items("共识", s.get("consensus")))
        points = [d.get("point") for d in s.get("disagreements") or [] if isinstance(d, dict)]
        extras += [_items("分歧", points), _items("仍需核实", s.get("open_questions"))]
        return s.get("final_answer") or "", extras
    if "mindmap" in by_kind:
        mm = _load(by_kind["mindmap"])
        return mm.get("document") or mm.get("markdown") or "", extras
    answers = [o for o in outputs if o.kind in ("revision", "answer")]
    revisions = [o for o in answers if o.kind == "revision"]
    pick = revisions or answers
    texts = [_load(o).get("answer", "") if o.kind == "revision" else o.content for o in pick]
    texts = [t for t in texts if t.strip()]
    if len(texts) == 1:
        return texts[0], extras
    return "\n\n".join(
        f"## 答案 {n}\n\n{shift_headings(t, 3)}" for n, t in enumerate(texts, 1)
    ), extras


def title_of(question: str) -> str:
    first = next((s.strip() for s in question.splitlines() if s.strip()), "") or "圆桌成果"
    first = re.sub(r"^#+\s*", "", first)
    return first if len(first) <= 40 else first[:39] + "…"


_PROCESS = {  # 过程里收录的产出：类型 → 小标题后缀
    "answer": "初稿",
    "revision": "修订后",
    "work": "成果",
    "office_work": "初稿",
    "office_fix": "修改稿",
    "office_speech": "发言",
}


def _process(outputs: list[OutputView]) -> str:
    """附录：各成员的初稿、修订、分工成果与发言（不含互评），按产生顺序。"""
    parts: list[str] = []
    for o in outputs:
        if o.kind not in _PROCESS:
            continue
        data = _load(o) if o.kind != "answer" else {}
        text = o.content if o.kind == "answer" else data.get("answer") or data.get("text") or ""
        if not text.strip():
            continue
        who = o.code or "成员"
        if o.kind == "work" and data.get("subtask"):
            who = f"{data['subtask']} · {who}"
        parts.append(f"### {who} {_PROCESS[o.kind]}\n\n{shift_headings(text.strip(), 4)}")
    return "\n\n".join(parts)


def deliverable(
    view: SessionView, *, question: bool = True, process: bool = False
) -> Document | None:
    """整理一场的最终成果；还没有任何成果时返回 None。

    question：开头附上题目原文；process：文末附上过程（各人的初稿、修订、发言）。"""
    tables = sorted({o.table_no for o in view.outputs})
    parts: list[str] = []
    for table_no in tables:
        body, extras = _table([o for o in view.outputs if o.table_no == table_no])
        if not body.strip():
            continue
        extras = [e for e in extras if e]
        if len(tables) > 1:
            parts.append(f"## 第 {table_no + 1} 张桌子\n\n{shift_headings(body.strip(), 3)}")
            parts += [shift_headings(e, 3) for e in extras]
        else:
            parts.append(shift_headings(body.strip(), 2))
            parts += extras
    if not parts:
        return None
    if process:
        steps = [_process([o for o in view.outputs if o.table_no == t]) for t in tables]
        if any(steps):
            parts.append("## 附：过程\n\n" + "\n\n".join(s for s in steps if s))
    title = title_of(view.question)
    head = f"# {title}\n\n"
    if question and view.question.strip() != title:
        quoted = "\n".join(
            f"> {s}" if s.strip() else ">" for s in view.question.strip().split("\n")
        )
        head += f"{quoted}\n\n"
    return Document(title, view.question, head + "\n\n".join(parts) + "\n")
