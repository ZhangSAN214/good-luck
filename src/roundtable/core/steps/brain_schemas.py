"""头脑风暴（R16）的数据结构与解析：想法 → 拓展 → 统筹整合成思维导图。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from roundtable.core.jsonout import extract_json_object
from roundtable.core.mindmap import Branch, MindMap, Node

EXPAND_KINDS = ("build", "combine", "challenge", "new")  # 深化 / 组合 / 质疑改进 / 全新


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class IdeaOut(_Loose):
    title: str = ""
    detail: str = ""


class IdeateOutput(_Loose):
    ideas: list[IdeaOut] = Field(default_factory=list)


@dataclass(frozen=True)
class Idea:
    id: str
    title: str
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "detail": self.detail}


def parse_ideas(text: str, max_ideas: int) -> tuple[tuple[str, str], ...]:
    out = IdeateOutput.model_validate(extract_json_object(text))
    ideas = [(i.title.strip(), i.detail.strip()) for i in out.ideas if i.title.strip()][:max_ideas]
    if not ideas:
        raise ValueError("没有给出任何想法")
    return tuple(ideas)


class ExpansionOut(_Loose):
    on: str = ""
    kind: str = "build"
    text: str = ""


class ExpandOutput(_Loose):
    items: list[ExpansionOut] = Field(default_factory=list)


@dataclass(frozen=True)
class Expansion:
    on: str  # 针对的想法编号；新想法为空
    kind: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"on": self.on, "kind": self.kind, "text": self.text}


def parse_expansions(text: str, known_ids: Sequence[str], max_items: int) -> tuple[Expansion, ...]:
    """每条拓展针对别人的某个想法（编号必须存在）；kind=new 的不需要编号。没有也可以（返回空）。"""
    out = ExpandOutput.model_validate(extract_json_object(text))
    known = {i.upper() for i in known_ids}
    items = []
    for x in out.items:
        body, on = x.text.strip(), x.on.strip().upper()
        kind = x.kind.strip().lower() if x.kind.strip().lower() in EXPAND_KINDS else "build"
        if not body:
            continue
        if kind == "new":
            on = ""
        elif on not in known:
            continue
        items.append(Expansion(on, kind, body))
    return tuple(items[:max_items])


class OrgIdea(_Loose):
    text: str = ""
    refs: list[str] = Field(default_factory=list)


class OrgBranch(_Loose):
    name: str = ""
    ideas: list[OrgIdea] = Field(default_factory=list)


class OrganizeOutput(_Loose):
    title: str = ""
    branches: list[OrgBranch] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)


def parse_mindmap(
    text: str,
    idea_authors: dict[str, str],
    label: Callable[[str], str],
    *,
    max_branches: int,
    max_ideas_per_branch: int,
    max_next_steps: int,
) -> MindMap:
    """统筹整合的 JSON → 思维导图。refs 里的想法编号换成提出者的称呼；不存在的编号忽略。"""
    out = OrganizeOutput.model_validate(extract_json_object(text))
    branches = []
    for b in out.branches:
        nodes = []
        for i in b.ideas:
            if not i.text.strip():
                continue
            authors: list[str] = []
            for ref in i.refs:
                code = idea_authors.get(ref.strip().upper())
                if code and label(code) not in authors:
                    authors.append(label(code))
            nodes.append(Node(i.text.strip(), tuple(authors)))
        if b.name.strip() and nodes:
            branches.append(Branch(b.name.strip(), tuple(nodes[:max_ideas_per_branch])))
    if not branches:
        raise ValueError("没有整合出任何分支")
    steps = tuple(s.strip() for s in out.next_steps if s.strip())[:max_next_steps]
    return MindMap(out.title.strip() or "头脑风暴", tuple(branches[:max_branches]), steps)


def fallback_mindmap(
    title: str,
    ideas: dict[str, tuple[Idea, ...]],
    label: Callable[[str], str],
    *,
    max_branches: int,
    max_ideas_per_branch: int,
) -> MindMap:
    """统筹整合失败：按成员分组列出每个人的想法（降级，仍然可用）。"""
    branches = [
        Branch(f"{label(code)} 的想法", tuple(Node(i.title) for i in items[:max_ideas_per_branch]))
        for code, items in sorted(ideas.items())
        if items
    ]
    return MindMap(title or "头脑风暴", tuple(branches[:max_branches]), degraded=True)


@dataclass
class BrainState:
    ideas: dict[str, tuple[Idea, ...]] = field(default_factory=dict)  # 代号 → 想法
    expansions: dict[str, tuple[Expansion, ...]] = field(default_factory=dict)
    mindmap: MindMap | None = None
    svg: str = ""
    pending_calls: dict[str, int | None] = field(default_factory=dict)  # 临时：代号 → 调用编号

    def all_ideas(self) -> list[tuple[str, Idea]]:
        return [(code, i) for code, items in sorted(self.ideas.items()) for i in items]

    def authors(self) -> dict[str, str]:
        return {i.id: code for code, i in self.all_ideas()}


def restore_brain(state: BrainState, kind: str, code: str | None, data: dict[str, Any]) -> bool:
    """从已保存的产出恢复；返回是否是头脑风暴的产出。"""
    if kind == "ideas" and code:
        state.ideas[code] = tuple(Idea(**i) for i in data["ideas"])
    elif kind == "expansions" and code:
        state.expansions[code] = tuple(Expansion(**i) for i in data["items"])
    elif kind == "mindmap":
        state.mindmap = MindMap.from_dict(data["map"])
        state.svg = data.get("svg", "")
    else:
        return False
    return True
