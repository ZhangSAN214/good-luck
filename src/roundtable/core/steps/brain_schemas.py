"""头脑风暴（R16）的数据结构与解析：想法 → 拓展 / 碰撞杂交 → 投票 → 统筹整合成思维导图与点子榜。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from roundtable.core.ideaboard import Ballot, Board, Candidate, CompareRow, Pick, Plan, clamp_score
from roundtable.core.jsonout import extract_json_object
from roundtable.core.mindmap import Branch, MindMap, Node

EXPAND_KINDS = ("build", "combine", "challenge", "new")  # 深化 / 组合 / 质疑改进 / 全新
CROSS_KINDS = ("hybrid", "flip", "fix")  # 杂交 / 反转放大 / 挑刺补救
# 拓展里本身就是一个新点子、可以被投票的种类（其余是对原点子的补充或质疑）
CANDIDATE_KINDS = ("hybrid", "flip", "new", "combine")


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
    also: str = ""  # 杂交的另一个想法编号

    def to_dict(self) -> dict[str, Any]:
        d = {"on": self.on, "kind": self.kind, "text": self.text}
        if self.also:
            d["also"] = self.also
        return d


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


class CrossOut(_Loose):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    kind: str = ""
    on: str = ""
    with_: str = Field(default="", alias="with")
    text: str = ""


class CrossOutput(_Loose):
    items: list[CrossOut] = Field(default_factory=list)


def parse_crossbreed(text: str, known_ids: Sequence[str], max_items: int) -> tuple[Expansion, ...]:
    """碰撞杂交：hybrid 需要两个不同的已知编号（另一个无效时按 flip 处理）；flip / fix 需要一个。"""
    out = CrossOutput.model_validate(extract_json_object(text))
    known = {i.upper() for i in known_ids}
    items = []
    for x in out.items:
        body, on, other = x.text.strip(), x.on.strip().upper(), x.with_.strip().upper()
        kind = x.kind.strip().lower()
        if not body or kind not in CROSS_KINDS or on not in known:
            continue
        if kind == "hybrid" and (other not in known or other == on):
            kind = "flip"
        items.append(Expansion(on, kind, body, other if kind == "hybrid" else ""))
    return tuple(items[:max_items])


class PickOut(_Loose):
    id: str = ""
    novelty: Any = 3
    feasibility: Any = 3
    value: Any = 3


class VoteOutput(_Loose):
    picks: list[PickOut] = Field(default_factory=list)
    dark_horse: str = ""


def parse_ballot(text: str, known_ids: Sequence[str], max_picks: int) -> Ballot:
    """投票：编号必须在给出的范围内（不含自己的点子），去重，分数限制在 1～5；一票都没有时报错。"""
    out = VoteOutput.model_validate(extract_json_object(text))
    known = {i.upper() for i in known_ids}
    picks: list[Pick] = []
    seen: set[str] = set()
    for p in out.picks:
        pid = p.id.strip().upper()
        if pid not in known or pid in seen:
            continue
        seen.add(pid)
        picks.append(
            Pick(pid, clamp_score(p.novelty), clamp_score(p.feasibility), clamp_score(p.value))
        )
    if not picks:
        raise ValueError("没有投出任何有效的票")
    dark = out.dark_horse.strip().upper()
    return Ballot(tuple(picks[:max_picks]), dark if dark in known else "")


class CompareOut(_Loose):
    id: str = ""
    novelty: str = ""
    feasibility: str = ""
    cost: str = ""
    risk: str = ""


class StoryOut(_Loose):
    id: str = ""
    story: str = ""


class PlanOut(_Loose):
    id: str = ""
    experiment: str = ""
    steps: list[str] = Field(default_factory=list)


class BoardOutput(_Loose):
    compare: list[CompareOut] = Field(default_factory=list)
    stories: list[StoryOut] = Field(default_factory=list)
    plan: PlanOut | None = None
    dark_horse_why: str = ""


def parse_board_extras(
    text: str,
    *,
    top_ids: Sequence[str],
    story_ids: Sequence[str],
    plan_id: str,
    dark_horse: str,
) -> dict[str, Any]:
    """统筹为点子榜写的部分（对比表、场景故事、行动方案、黑马理由）；只收指定编号的，格式不对时为空。"""
    try:
        out = BoardOutput.model_validate(extract_json_object(text))
    except Exception:  # noqa: BLE001 — 这部分是锦上添花，导图能用就不重试
        return {}
    top = {i.upper() for i in top_ids}
    stories = {i.upper() for i in story_ids}
    compare = []
    for c in out.compare:
        cid = c.id.strip().upper()
        if cid in top and cid not in {x.id for x in compare}:
            cells = (c.novelty, c.feasibility, c.cost, c.risk)
            compare.append(CompareRow(cid, *(x.strip() for x in cells)))
    order = {i.upper(): n for n, i in enumerate(top_ids)}
    compare.sort(key=lambda c: order[c.id])
    told: dict[str, str] = {}
    for s in out.stories:
        sid = s.id.strip().upper()
        if sid in stories and s.story.strip() and sid not in told:
            told[sid] = s.story.strip()
    plan = None
    if out.plan and plan_id and out.plan.id.strip().upper() == plan_id.upper():
        steps = tuple(x.strip() for x in out.plan.steps if x.strip())[:5]
        if out.plan.experiment.strip() or steps:
            plan = Plan(plan_id.upper(), out.plan.experiment.strip(), steps)
    return {
        "compare": tuple(compare),
        "stories": tuple((i.upper(), told[i.upper()]) for i in story_ids if i.upper() in told),
        "plan": plan,
        "dark_horse_why": out.dark_horse_why.strip() if dark_horse else "",
    }


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
    lenses: dict[str, str] = field(default_factory=dict)  # 代号 → 抽到的发散手法
    votes: dict[str, Ballot] = field(default_factory=dict)  # 代号 → 选票
    mindmap: MindMap | None = None
    board: Board | None = None
    svg: str = ""
    pending_calls: dict[str, int | None] = field(default_factory=dict)  # 临时：代号 → 调用编号

    def all_ideas(self) -> list[tuple[str, Idea]]:
        return [(code, i) for code, items in sorted(self.ideas.items()) for i in items]

    def authors(self) -> dict[str, str]:
        return {i.id: code for code, i in self.all_ideas()}

    def numbered_expansions(self) -> list[tuple[str, str, Expansion]]:
        """拓展的编号（E1、E2……）：按代号排序依次编号，与谁先返回无关。"""
        out = []
        for code in sorted(self.expansions):
            for x in self.expansions[code]:
                out.append((f"E{len(out) + 1}", code, x))
        return out

    def candidates(self) -> list[Candidate]:
        """可以投票的点子：第一轮的点子 + 杂交 / 反转放大 / 全新 / 组合出的新点子。"""
        out = [
            Candidate(i.id, code, i.title + (f"：{i.detail}" if i.detail else ""))
            for code, i in self.all_ideas()
        ]
        for xid, code, x in self.numbered_expansions():
            if x.kind in CANDIDATE_KINDS:
                refs = tuple(r for r in (x.on, x.also) if r)
                out.append(Candidate(xid, code, x.text, x.kind, refs))
        return out


def restore_brain(state: BrainState, kind: str, code: str | None, data: dict[str, Any]) -> bool:
    """从已保存的产出恢复；返回是否是头脑风暴的产出。"""
    if kind == "ideas" and code:
        state.ideas[code] = tuple(Idea(**i) for i in data["ideas"])
        if data.get("lens"):
            state.lenses[code] = data["lens"]
    elif kind == "expansions" and code:
        state.expansions[code] = tuple(Expansion(**i) for i in data["items"])
    elif kind == "votes" and code:
        state.votes[code] = Ballot.from_dict(data)
    elif kind == "mindmap":
        state.mindmap = MindMap.from_dict(data["map"])
        state.svg = data.get("svg", "")
        if data.get("board"):
            state.board = Board.from_dict(data["board"])
    else:
        return False
    return True
