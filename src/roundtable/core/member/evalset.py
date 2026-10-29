"""评测集：每位成员同一套情境题，规则判分（docs/MEMBER_ENGINE.md「评测集」）。

- 题库在 ``benchmarks/members/scenarios.yaml``（pydantic 校验，多余字段报错，带文件名和字段路径）。
- 每道题把「自己的资料」（自我档案经上下文组装器挑过）和情境交给成员，提示词 ``member_eval``，
  参数 ``chat.engine.eval``。**所有成员的题目、提示词、参数、判分规则完全相同**，只有名字和
  她自己的资料不同（中立）。
- 判分只用规则（``max_chars`` / ``must_any`` / ``must_not_any`` / 题库共用的 ``common_bans``），
  不调用模型。另算「撞型」：同一道题两位成员的回复太像（字符二元组相似度 ≥ ``clash_similarity``）；
  标了 ``distinct`` 的题（「8 人回复不雷同」）撞型的两位都算不合格。
- 离线：测试里用假模型跑（$0）。真实模型的月度小样本以后接命令行。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from roundtable.core.member.context import Piece
from roundtable.core.member.profile import LOCKED, Profile
from roundtable.core.moments import similarity

DEFAULT_PATH = Path(__file__).resolve().parents[4] / "benchmarks" / "members" / "scenarios.yaml"
ROLE = "member_eval"


class EvalSetError(ValueError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Scenario(_Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    category: str = Field(min_length=1)
    situation: str = Field(min_length=1, max_length=500)
    max_chars: int = Field(default=200, ge=10, le=2000)
    must_any: list[str] = Field(default_factory=list)
    must_not_any: list[str] = Field(default_factory=list)
    distinct: bool = False  # 8 人回复不能雷同：撞型的成员这题算不合格（P5 表达）


class EvalSet(_Strict):
    common_bans: list[str] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(min_length=1)

    @field_validator("scenarios")
    @classmethod
    def _unique(cls, v: list[Scenario]) -> list[Scenario]:
        ids = [s.id for s in v]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"题目 id 重复：{', '.join(dup)}")
        return v


def load_evalset(path: Path = DEFAULT_PATH) -> EvalSet:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return EvalSet.model_validate(raw)
    except ValidationError as e:
        where = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors()
        )
        raise EvalSetError(f"{path.name}: {where}") from None
    except (OSError, yaml.YAMLError) as e:
        raise EvalSetError(f"{path.name}: {e}") from None


def judge(s: Scenario, text: str, common_bans: list[str] = ()) -> list[str]:
    """不合格的原因（空表示通过）。"""
    t = text.strip()
    out = []
    if not t:
        out.append("没有回复")
    if len(t) > s.max_chars:
        out.append(f"太长（{len(t)} > {s.max_chars} 字）")
    if s.must_any and not any(k in t for k in s.must_any):
        out.append(f"缺少：{' / '.join(s.must_any)}")
    hit = [k for k in [*s.must_not_any, *common_bans] if k in t]
    if hit:
        out.append(f"不该出现：{'、'.join(hit)}")
    return out


def profile_pieces(profile: Profile) -> list[Piece]:
    """自我档案 → 上下文的固定段（每个分区一段，按分区名排序，内容不变时前缀也不变）。"""
    out = []
    for sec in sorted(k for k in profile.data if k != LOCKED):
        items = profile.data[sec] or {}
        text = "；".join(f"{k}：{v}" for k, v in sorted(items.items()))
        if text:
            out.append(Piece("profile", f"{sec}｜{text}", fixed=True))
    return out


@dataclass
class Answer:
    member: str
    scenario: str
    category: str
    text: str
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass
class EvalReport:
    answers: list[Answer]
    clashes: list[dict[str, Any]]

    def score(self, member: str | None = None) -> float:
        rows = [a for a in self.answers if member is None or a.member == member]
        return sum(a.passed for a in rows) / len(rows) if rows else 0.0

    def by_category(self) -> dict[str, float]:
        cats: dict[str, list[Answer]] = {}
        for a in self.answers:
            cats.setdefault(a.category, []).append(a)
        return {c: sum(a.passed for a in rows) / len(rows) for c, rows in sorted(cats.items())}

    def summary(self) -> dict[str, Any]:
        members = sorted({a.member for a in self.answers})
        return {
            "score": round(self.score(), 3),
            "members": {m: round(self.score(m), 3) for m in members},
            "categories": {c: round(v, 3) for c, v in self.by_category().items()},
            "clashes": len(self.clashes),
            "failures": [
                {"member": a.member, "scenario": a.scenario, "why": a.failures}
                for a in self.answers
                if a.failures
            ],
        }


Call = Callable[[str, Any, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class Subject:
    """一位接受评测的成员：id、名字、用哪个模型、她的上下文段落（档案等）。"""

    member: str
    name: str
    model_id: str
    pieces: tuple[Piece, ...] = ()


async def run_eval(
    rt: Any,
    subjects: list[Subject],
    evalset: EvalSet,
    *,
    assemble: Callable[[list[Piece]], Any],
    call: Call | None = None,
) -> EvalReport:
    """依次让每位成员答每道题。call 为空时走渠道路由（真实调用，会花钱）。"""
    rules = rt.config.roundtable.chat.engine.eval
    params = {"max_tokens": rules.max_tokens}
    version = rt.config.roundtable.prompts[ROLE]

    async def default(model_id: str, messages: Any, p: dict[str, Any]) -> str:
        return (await rt.router.complete(model_id, messages, p)).text

    ask = call or default
    answers: list[Answer] = []
    for subject in subjects:
        context = assemble(list(subject.pieces)).text or "（暂无）"
        for s in evalset.scenarios:
            prompt = rt.prompts.render(
                ROLE, version, name=subject.name, context=context, situation=s.situation
            )
            text = (await ask(subject.model_id, prompt.messages, dict(params))).strip()
            answers.append(
                Answer(subject.member, s.id, s.category, text, judge(s, text, evalset.common_bans))
            )
    clashes = []
    distinct = {s.id: s.distinct for s in evalset.scenarios}
    by_scenario: dict[str, list[Answer]] = {}
    for a in answers:
        by_scenario.setdefault(a.scenario, []).append(a)
    for sid, rows in by_scenario.items():
        for a, b in combinations(rows, 2):
            sim = similarity(a.text, b.text)
            if sim >= rules.clash_similarity:
                pair = [a.member, b.member]
                clashes.append({"scenario": sid, "members": pair, "similarity": sim})
                if distinct.get(sid):
                    a.failures.append(f"和 {b.member} 的回复雷同")
                    b.failures.append(f"和 {a.member} 的回复雷同")
    return EvalReport(answers, clashes)
