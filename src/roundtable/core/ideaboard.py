"""点子榜（头脑风暴·灵感爆发）：投票排名、对比表、场景故事、行动方案、黑马。

纯数据与字符串处理，不调用模型，也不依赖任何模型或厂商。排名只由代码按组员的匿名投票计算：
总分 = 每张选票的 新颖 + 可行 + 价值 之和；同分看票数，再按编号顺序。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

SCORE_MIN, SCORE_MAX = 1, 5


@dataclass(frozen=True)
class Candidate:
    """可以被投票的点子：第一轮的点子（I 编号）或碰撞杂交出的新点子（E 编号）。"""

    id: str
    code: str  # 提出者代号
    text: str
    kind: str = "idea"  # idea / hybrid / flip / new / combine
    refs: tuple[str, ...] = ()  # 由哪些点子演变而来


@dataclass(frozen=True)
class Pick:
    id: str
    novelty: int
    feasibility: int
    value: int

    @property
    def total(self) -> int:
        return self.novelty + self.feasibility + self.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "novelty": self.novelty,
            "feasibility": self.feasibility,
            "value": self.value,
        }


@dataclass(frozen=True)
class Ballot:
    picks: tuple[Pick, ...] = ()
    dark_horse: str = ""
    degraded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "picks": [p.to_dict() for p in self.picks],
            "dark_horse": self.dark_horse,
            "degraded": self.degraded,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Ballot:
        return cls(
            tuple(Pick(**p) for p in d.get("picks", ())),
            d.get("dark_horse", ""),
            bool(d.get("degraded", False)),
        )


@dataclass(frozen=True)
class Ranked:
    id: str
    text: str
    authors: tuple[str, ...]  # 称呼
    kind: str
    refs: tuple[str, ...]
    score: int
    votes: int
    novelty: float  # 平均新颖分（没有票时为 0）
    dark_votes: int = 0
    feasibility: float = 0.0  # 平均可行分（没有票时为 0）
    value: float = 0.0  # 平均价值分（没有票时为 0）

    @property
    def voted(self) -> bool:
        return self.votes > 0 or self.dark_votes > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "authors": list(self.authors),
            "kind": self.kind,
            "refs": list(self.refs),
            "score": self.score,
            "votes": self.votes,
            "novelty": round(self.novelty, 2),
            "dark_votes": self.dark_votes,
            "feasibility": round(self.feasibility, 2),
            "value": round(self.value, 2),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Ranked:
        return cls(
            d["id"],
            d["text"],
            tuple(d.get("authors", ())),
            d.get("kind", "idea"),
            tuple(d.get("refs", ())),
            int(d.get("score", 0)),
            int(d.get("votes", 0)),
            float(d.get("novelty", 0)),
            int(d.get("dark_votes", 0)),
            float(d.get("feasibility", 0)),
            float(d.get("value", 0)),
        )


def clamp_score(v: Any) -> int:
    try:
        n = round(float(v))
    except (TypeError, ValueError):
        return 3
    return max(SCORE_MIN, min(SCORE_MAX, n))


def rank(
    candidates: Sequence[Candidate],
    ballots: Mapping[str, Ballot],
    label,
) -> list[Ranked]:
    """按投票排名：得票（含黑马票）的点子在前，没人投的按编号顺序排在最后（灵感墙、谱系要用到全部点子）。
    投票人不能投自己的点子（这里再过滤一次）。"""
    by_id = {c.id: c for c in candidates}
    order = {c.id: i for i, c in enumerate(candidates)}
    score: dict[str, int] = {}
    votes: dict[str, int] = {}
    novelty: dict[str, int] = {}
    feasible: dict[str, int] = {}
    worth: dict[str, int] = {}
    dark: dict[str, int] = {}
    for voter, ballot in sorted(ballots.items()):
        for p in ballot.picks:
            c = by_id.get(p.id)
            if c is None or c.code == voter:
                continue
            score[p.id] = score.get(p.id, 0) + p.total
            votes[p.id] = votes.get(p.id, 0) + 1
            novelty[p.id] = novelty.get(p.id, 0) + p.novelty
            feasible[p.id] = feasible.get(p.id, 0) + p.feasibility
            worth[p.id] = worth.get(p.id, 0) + p.value
        c = by_id.get(ballot.dark_horse)
        if c is not None and c.code != voter:
            dark[c.id] = dark.get(c.id, 0) + 1
    ids = sorted(
        by_id, key=lambda i: (-score.get(i, 0), -votes.get(i, 0), -dark.get(i, 0), order[i])
    )

    def avg(total: dict[str, int], i: str) -> float:
        return total.get(i, 0) / votes[i] if votes.get(i) else 0.0

    return [
        Ranked(
            i,
            by_id[i].text,
            (label(by_id[i].code),),
            by_id[i].kind,
            by_id[i].refs,
            score.get(i, 0),
            votes.get(i, 0),
            avg(novelty, i),
            dark.get(i, 0),
            avg(feasible, i),
            avg(worth, i),
        )
        for i in ids
    ]


def pick_dark_horse(ranking: Sequence[Ranked], top: int) -> Ranked | None:
    """黑马：不在前 top 名里、黑马票最多的点子（同票看平均新颖分，再看排名）；没人投黑马时没有。"""
    best = {r.id for r in [r for r in ranking if r.votes][:top]}
    rest = [(i, r) for i, r in enumerate(ranking) if r.dark_votes > 0 and r.id not in best]
    if not rest:
        return None
    return max(rest, key=lambda x: (x[1].dark_votes, x[1].novelty, -x[0]))[1]


@dataclass(frozen=True)
class CompareRow:
    id: str
    novelty: str = ""
    feasibility: str = ""
    cost: str = ""
    risk: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "novelty": self.novelty,
            "feasibility": self.feasibility,
            "cost": self.cost,
            "risk": self.risk,
        }


@dataclass(frozen=True)
class Plan:
    id: str
    experiment: str = ""
    steps: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "experiment": self.experiment, "steps": list(self.steps)}


@dataclass(frozen=True)
class Board:
    ranking: tuple[Ranked, ...] = ()
    top: int = 5
    compare: tuple[CompareRow, ...] = ()
    stories: tuple[tuple[str, str], ...] = ()  # (编号, 故事)
    plan: Plan | None = None
    dark_horse: str = ""  # 编号
    dark_horse_why: str = ""
    lenses: dict[str, str] = field(default_factory=dict)  # 称呼 → 抽到的发散手法

    def find(self, idea_id: str) -> Ranked | None:
        return next((r for r in self.ranking if r.id == idea_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranking": [r.to_dict() for r in self.ranking],
            "top": self.top,
            "compare": [c.to_dict() for c in self.compare],
            "stories": [{"id": i, "story": s} for i, s in self.stories],
            "plan": self.plan.to_dict() if self.plan else None,
            "dark_horse": self.dark_horse,
            "dark_horse_why": self.dark_horse_why,
            "lenses": dict(self.lenses),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Board:
        plan = d.get("plan")
        return cls(
            tuple(Ranked.from_dict(r) for r in d.get("ranking", ())),
            int(d.get("top", 5)),
            tuple(CompareRow(**c) for c in d.get("compare", ())),
            tuple((s["id"], s["story"]) for s in d.get("stories", ())),
            Plan(plan["id"], plan.get("experiment", ""), tuple(plan.get("steps", ())))
            if plan
            else None,
            d.get("dark_horse", ""),
            d.get("dark_horse_why", ""),
            dict(d.get("lenses", {})),
        )


# --- Markdown ---------------------------------------------------------------------


def _cell(t: str) -> str:
    return t.replace("|", "／").replace("\n", " ").strip()


def _short(t: str, n: int = 40) -> str:
    t = t.replace("\n", " ").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def to_markdown(b: Board) -> str:
    """点子榜的 Markdown（放在思维导图大纲之前，作为最终答案的一部分）。"""
    lines: list[str] = []
    if any(r.voted for r in b.ranking):
        lines += ["## 点子榜（组员匿名投票）", ""]
        for n, r in enumerate([r for r in b.ranking if r.voted][: max(b.top * 2, 10)], 1):
            by = "、".join(r.authors)
            src = f"，由 {' × '.join(r.refs)} 而来" if r.refs else ""
            lines.append(f"{n}. **{r.id}** {r.text}（{r.score} 分 · {r.votes} 票 · {by}{src}）")
        lines.append("")
    if b.compare:
        lines += ["## 前几名对比", "", "| 点子 | 新颖 | 可行 | 投入 | 风险 |"]
        lines.append("|---|---|---|---|---|")
        for c in b.compare:
            r = b.find(c.id)
            name = _cell(f"{c.id} {_short(r.text, 24)}" if r else c.id)
            cells = (c.novelty, c.feasibility, c.cost, c.risk)
            lines.append(f"| {name} | " + " | ".join(_cell(x) for x in cells) + " |")
        lines.append("")
    if b.stories:
        lines += ["## 场景故事", ""]
        for i, story in b.stories:
            r = b.find(i)
            lines += [f"**{i} {_short(r.text) if r else ''}**".rstrip(), "", story, ""]
    if b.plan:
        r = b.find(b.plan.id)
        lines += [f"## 行动方案：{b.plan.id} {_short(r.text) if r else ''}".rstrip(), ""]
        if b.plan.experiment:
            lines += [f"**最小试验**：{b.plan.experiment}", ""]
        lines += [f"{n}. {s}" for n, s in enumerate(b.plan.steps, 1)]
        lines.append("")
    if b.dark_horse:
        r = b.find(b.dark_horse)
        lines += ["## 黑马", "", f"**{b.dark_horse}** {r.text if r else ''}".rstrip()]
        if b.dark_horse_why:
            lines += ["", b.dark_horse_why]
        lines.append("")
    return "\n".join(lines)
