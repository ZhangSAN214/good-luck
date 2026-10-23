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


@dataclass(frozen=True)
class BenchMode:
    workflow: str
    tier: str

    @property
    def label(self) -> str:
        return f"{self.workflow}/{self.tier}"


def parse_modes(spec: str) -> list[BenchMode]:
    """ "solo:budget,discussion:budget,collab:flagship" → 模式列表。"""
    modes = []
    for part in (s.strip() for s in spec.split(",") if s.strip()):
        workflow, _, tier = part.partition(":")
        modes.append(BenchMode(workflow, tier or "budget"))
    if not modes:
        raise BenchError("至少要有一个模式")
    return modes


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
                run = await self._run_one(q, mode, seed)
                seconds = self.clock() - started
                if run is None:
                    r = BenchResult(
                        batch, q.id, q.category, mode.workflow, mode.tier, None, "error",
                        None, 0.0, seconds,
                    )  # fmt: skip
                else:
                    done = run.status == "completed"
                    r = BenchResult(
                        batch, q.id, q.category, mode.workflow, mode.tier, run.session_id,
                        run.status, judge(run.final_answer, q.judge) if done else None,
                        run.cost_usd, seconds,
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
