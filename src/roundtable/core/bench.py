"""对比实验（R14）：同一批题分别用个人作答 / 讨论 / 协同跑，记录对错、花费、耗时。

题库在 benchmarks/*.yaml：题目文字发给模型，判分方式和标准答案不发。判分只用规则（数字、
精确、关键词），不调用任何模型，所以判分本身不花钱，也不涉及厂商。本模块只看档位和模式，
不针对任何具体模型或厂商。
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from roundtable.core.orchestrator import Orchestrator, OrchestratorError, RunResult
from roundtable.core.routing import Question, RoutingError, UserChoice
from roundtable.core.storage import Repository


class BenchError(ValueError):
    """题库格式不对，或实验参数无效。"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Judge(_Strict):
    type: Literal["number", "exact", "keywords"]
    answer: float | str | None = None
    tol: float = Field(default=1e-6, ge=0)
    all: list[str] = Field(default_factory=list)
    any: list[str] = Field(default_factory=list)
    max_chars: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _complete(self) -> Judge:
        if self.type == "number" and not isinstance(self.answer, (int, float)):
            raise ValueError("number 判分需要数字 answer")
        if self.type == "exact" and not isinstance(self.answer, str):
            raise ValueError("exact 判分需要文字 answer")
        if self.type == "keywords" and not (self.all or self.any):
            raise ValueError("keywords 判分至少要有 all 或 any")
        return self


class BenchQuestion(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    category: str
    text: str = Field(min_length=1)
    judge: Judge


class BenchSet(_Strict):
    questions: list[BenchQuestion]

    @model_validator(mode="after")
    def _unique(self) -> BenchSet:
        ids = [q.id for q in self.questions]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"题目编号重复：{dup}")
        return self


def load_questions(path: str | Path) -> list[BenchQuestion]:
    """读取一个题库文件，或一个目录下的全部 *.yaml。格式不对时报出文件名和字段路径。"""
    p = Path(path)
    files = sorted(p.glob("*.yaml")) if p.is_dir() else [p]
    if not files:
        raise BenchError(f"{p} 里没有题库文件")
    out: list[BenchQuestion] = []
    for f in files:
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
            out.extend(BenchSet.model_validate(data).questions)
        except (OSError, yaml.YAMLError) as exc:
            raise BenchError(f"{f.name}：无法读取（{exc}）") from None
        except ValueError as exc:
            raise BenchError(f"{f.name}：{exc}") from None
    ids = [q.id for q in out]
    if len(set(ids)) != len(ids):
        raise BenchError("多个题库文件之间题目编号重复")
    return out


# --- 判分 ------------------------------------------------------------------------

_NUM = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")
_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def last_number(text: str) -> float | None:
    """答案末尾 200 字里的最后一个数字（去掉千分位逗号）。"""
    found = _NUM.findall(text[-200:])
    return float(found[-1].replace(",", "")) if found else None


def judge(answer: str | None, spec: Judge) -> bool:
    if not answer or not answer.strip():
        return False
    if spec.type == "number":
        got = last_number(answer)
        return got is not None and abs(got - float(spec.answer or 0)) <= spec.tol
    if spec.type == "exact":
        return _PUNCT.sub("", answer).lower() == _PUNCT.sub("", str(spec.answer)).lower()
    if spec.max_chars is not None and len(answer) > spec.max_chars:
        return False
    if any(k not in answer for k in spec.all):
        return False
    return not spec.any or any(k in answer for k in spec.any)


# --- 运行 ------------------------------------------------------------------------


VOTE = "vote"  # 自洽投票：个人作答跑 samples 次，取多数答案（对照基线，不是正式模式）
VOTE_SAMPLES = 5
VOTE_MAX_SAMPLES = 9


@dataclass(frozen=True)
class BenchMode:
    workflow: str
    tier: str
    samples: int = 1  # 大于 1 时是自洽投票：个人作答跑 samples 次取多数

    @property
    def name(self) -> str:
        """记录和报告里的模式名：自洽投票写成 vote5，其余就是流程名。"""
        return f"{VOTE}{self.samples}" if self.samples > 1 else self.workflow

    @property
    def label(self) -> str:
        return f"{self.name}/{self.tier}"


def parse_modes(spec: str) -> list[BenchMode]:
    """ "solo:budget,discussion:budget,vote:budget:5" → 模式列表。

    vote:档位[:次数] 是自洽投票基线：同一题用个人作答跑几次（默认 5），取出现最多的答案。
    """
    modes = []
    for part in (s.strip() for s in spec.split(",") if s.strip()):
        workflow, _, rest = part.partition(":")
        tier, _, samples = rest.partition(":")
        if workflow != VOTE:
            if samples:
                raise BenchError(f"「{part}」：只有 vote 可以写次数")
            modes.append(BenchMode(workflow, tier or "budget"))
            continue
        try:
            n = int(samples) if samples else VOTE_SAMPLES
        except ValueError:
            raise BenchError(f"「{part}」：次数要是整数") from None
        if not 2 <= n <= VOTE_MAX_SAMPLES:
            raise BenchError(f"「{part}」：投票次数要在 2～{VOTE_MAX_SAMPLES} 之间")
        modes.append(BenchMode("solo", tier or "budget", n))
    if not modes:
        raise BenchError("至少要有一个模式")
    return modes


def _vote_key(answer: str, spec: Judge) -> str | None:
    """投票时怎样算「同一个答案」：只按题型（数字 / 精确）取出答案本身，不看标准答案。"""
    if spec.type == "number":
        got = last_number(answer)
        return None if got is None else f"{got:.6g}"
    if spec.type == "exact":
        return _PUNCT.sub("", answer).lower() or None
    return None  # 开放题没法按答案计票


def majority(answers: Sequence[str | None], spec: Judge) -> str | None:
    """自洽投票：出现次数最多的答案（并列取先出现的）；开放题或都取不出答案时用第一份。"""
    valid = [a for a in answers if a and a.strip()]
    if not valid:
        return None
    counts: dict[str, int] = {}
    first: dict[str, str] = {}
    for a in valid:
        key = _vote_key(a, spec)
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, a)
    if not counts:
        return valid[0]
    best = max(counts, key=lambda k: counts[k])  # max 在并列时保留最先插入的
    return first[best]


@dataclass(frozen=True)
class BenchResult:
    batch: str
    question_id: str
    category: str
    workflow: str
    tier: str
    session_id: str | None
    status: str
    correct: bool | None
    cost_usd: float
    seconds: float


# 实验中自动回答确认卡片：继续、不升级（要测的是该模式本身）；办公室模式照经理的安排办
# （开会 / 项目分工超过上限也照办），其余一律停止
AUTO_RESPONSE = {"cost": "continue", "members": "continue", "escalation": "accept"}


def auto_response(card: dict) -> str:
    if card["kind"] == "office":
        return str(card["recommendation"])
    return AUTO_RESPONSE.get(card["kind"], "stop")


class BenchRunner:
    def __init__(self, orc: Orchestrator, repo: Repository, *, clock=time.monotonic) -> None:
        self.orc, self.repo, self.clock = orc, repo, clock

    async def _run_one(self, q: BenchQuestion, mode: BenchMode, seed: int) -> RunResult | None:
        try:
            choice = UserChoice(mode.tier, workflow=mode.workflow)
            result = await self.orc.start(Question(q.text), choice, seed=seed, anonymous=False)
            while result.checkpoint is not None:
                card = result.checkpoint.card
                result = await self.orc.respond(result.session_id, auto_response(card))
            return result
        except (RoutingError, OrchestratorError):
            return None

    async def _run_single(
        self, q: BenchQuestion, mode: BenchMode, seed: int
    ) -> tuple[str | None, str, bool | None, float]:
        run = await self._run_one(q, mode, seed)
        if run is None:
            return None, "error", None, 0.0
        done = run.status == "completed"
        correct = judge(run.final_answer, q.judge) if done else None
        return run.session_id, run.status, correct, run.cost_usd

    async def _run_vote(
        self, q: BenchQuestion, mode: BenchMode, seed: int, budget_left: float
    ) -> tuple[str | None, str, bool | None, float]:
        """自洽投票：个人作答跑 mode.samples 次（种子依次加 1），取多数答案判分。

        花到 budget_left 就不再多跑，用已有的几份投票。返回 (首个会话 id, 状态, 对错, 花费)。
        """
        answers: list[str | None] = []
        first_id: str | None = None
        status, cost = "error", 0.0
        for i in range(mode.samples):
            if i and cost >= budget_left:
                break
            run = await self._run_one(q, mode, seed + i)
            if run is None:
                continue
            first_id = first_id or run.session_id
            cost += run.cost_usd
            if run.status == "completed":
                status = "completed"
                answers.append(run.final_answer)
            elif status != "completed":
                status = run.status
        if status != "completed":
            return first_id, status, None, cost
        return first_id, status, judge(majority(answers, q.judge), q.judge), cost

    async def run(
        self,
        questions: Sequence[BenchQuestion],
        modes: Sequence[BenchMode],
        *,
        batch: str,
        seed: int = 0,
        max_usd: float = 2.0,
        on_result: Callable[[BenchResult], None] | None = None,
    ) -> list[BenchResult]:
        """逐题逐模式运行；累计花费达到 max_usd 就停止（已完成的结果保留）。"""
        if max_usd <= 0:
            raise BenchError("max_usd 必须大于 0")
        spent = 0.0
        results: list[BenchResult] = []
        for q in questions:
            for mode in modes:
                if spent >= max_usd:
                    return results
                started = self.clock()
                if mode.samples > 1:
                    sid, status, correct, cost = await self._run_vote(
                        q, mode, seed, max_usd - spent
                    )
                else:
                    sid, status, correct, cost = await self._run_single(q, mode, seed)
                r = BenchResult(
                    batch, q.id, q.category, mode.name, mode.tier, sid, status, correct,
                    cost, self.clock() - started,
                )  # fmt: skip
                spent += r.cost_usd
                self.repo.add_bench_run(**r.__dict__)
                results.append(r)
                if on_result:
                    on_result(r)
        return results


# --- 报告 ------------------------------------------------------------------------


def summarize(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 模式 × 类别 汇总：题数、答对数、正确率、平均花费、平均耗时。只统计跑完的。"""
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        if r["status"] != "completed":
            continue
        groups.setdefault((r["workflow"], r["tier"], r["category"]), []).append(r)
    out = []
    for (workflow, tier, category), rs in sorted(groups.items()):
        n, right = len(rs), sum(1 for r in rs if r["correct"])
        out.append(
            {
                "workflow": workflow,
                "tier": tier,
                "category": category,
                "questions": n,
                "correct": right,
                "accuracy": right / n,
                "avg_cost_usd": sum(r["cost_usd"] for r in rs) / n,
                "avg_seconds": sum(r["seconds"] for r in rs) / n,
            }
        )
    return out


def cost_per_correct(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], float | None]:
    """每个模式答对一道题平均要花多少钱（没答对则为 None）。"""
    total: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        if r["status"] == "completed":
            total.setdefault((r["workflow"], r["tier"]), []).append(r)  # type: ignore[arg-type]
    result: dict[tuple[str, str], float | None] = {}
    for key, rs in total.items():
        right = sum(1 for r in rs if r["correct"])  # type: ignore[index]
        spent = sum(r["cost_usd"] for r in rs)  # type: ignore[index]
        result[key] = spent / right if right else None
    return result
