"""执行前的花费预估。token 数是粗估，参数在 routing.yaml 的 estimate 段，可按实际记录调整。"""

from __future__ import annotations

import unicodedata
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from roundtable.core.config import Price
from roundtable.core.config.schema import EstimateParams
from roundtable.core.providers import estimate_cost

# 不在此表中的步骤（例如以后新增的插件）不计费，并在结果中列出
KNOWN_STEPS = frozenset(
    {
        "answer",
        "review",
        "revise",
        "synthesize",
        "reveal",
        "decompose",
        "volunteer",
        "assign",
        "work",
        "cross_review",
        "rework",
        "merge",
    }
)


def text_tokens(text: str, params: EstimateParams) -> int:
    cjk = sum(1 for ch in text if unicodedata.east_asian_width(ch) in ("W", "F"))
    other = len(text) - cjk
    return round(cjk / params.cjk_chars_per_token + other / params.other_chars_per_token)


@dataclass(frozen=True)
class StepCost:
    step: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    max_usd: float = 0.0  # 每次调用都写满该步骤的输出长度上限时


@dataclass(frozen=True)
class CostEstimate:
    total_usd: float
    input_tokens: int
    output_tokens: int
    steps: tuple[StepCost, ...]
    unknown_steps: tuple[str, ...] = field(default_factory=tuple)
    max_usd: float = 0.0  # 上限：所有调用都写满输出长度上限（不含格式重试）
    calibrated: bool = False  # 是否用了历史记录校准


@dataclass(frozen=True)
class Participant:
    """参与估价的一个座位：模型 id（用于查历史）、档位（用于默认倍数）、价格。"""

    model_id: str
    tier: str | None
    price: Price


@dataclass(frozen=True)
class TokenStat:
    input_tokens: int
    output_tokens: int
    samples: int


@dataclass(frozen=True)
class EstimateHistory:
    """历史调用的统计（来自本机数据库）：真实的输出长度（含推理 token）和重试 / 重做的频率。

    tokens：(模型 id, 步骤) → 单次调用的 token 中位数；
    calls_per_slot：步骤 → 平均每个座位调用了几次（≥ 1，含格式重试与打回重做）。
    """

    tokens: dict[tuple[str, str], TokenStat] = field(default_factory=dict)
    calls_per_slot: dict[str, float] = field(default_factory=dict)


def history_from_calls(rows: Sequence[Mapping[str, Any]], min_samples: int) -> EstimateHistory:
    """由调用记录（repo.call_samples()）计算历史统计。失败且没有产出 token 的调用不计。"""
    per_model: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    per_slot: dict[str, Counter[tuple[Any, ...]]] = defaultdict(Counter)
    for r in rows:
        billed = r["error"] is None or r["output_tokens"] > 0
        if not billed:
            continue
        step = r["step"]
        slot = (r["session_id"], r["table_no"], r["code"] or r["role"])
        per_slot[step][slot] += 1
        if r["error"] is None and r["output_tokens"] > 0:
            per_model[(r["model_id"], step)].append((r["input_tokens"], r["output_tokens"]))
    tokens = {
        key: TokenStat(
            round(median(i for i, _ in vals)), round(median(o for _, o in vals)), len(vals)
        )
        for key, vals in per_model.items()
    }
    calls_per_slot = {
        step: max(1.0, sum(slots.values()) / len(slots))
        for step, slots in per_slot.items()
        if len(slots) >= min_samples
    }
    return EstimateHistory(tokens, calls_per_slot)


class _Pricer:
    def __init__(
        self,
        params: EstimateParams,
        history: EstimateHistory | None,
        caps: dict[str, int],
    ) -> None:
        self.params = params
        self.history = history or EstimateHistory()
        self.caps = caps
        self.calibrated = False

    def calls(
        self, step: str, people: Sequence[Participant], tokens_in: int, tokens_out: int
    ) -> tuple[int, int, float, float]:
        """一组人各调用一次：返回 (输入 token, 输出 token, 预计花费, 上限花费)。"""
        tin_total = tout_total = 0
        cost = upper = 0.0
        repeat = self.history.calls_per_slot.get(step, 1.0)
        cap = self.caps.get(step)
        for p in people:
            stat = self.history.tokens.get((p.model_id, step))
            if stat is not None and stat.samples >= self.params.history_min_samples:
                # 输入随题目和前序产出变化：取公式与历史的较大者；输出用历史中位数（含推理 token）
                tin, tout = max(tokens_in, stat.input_tokens), stat.output_tokens
                self.calibrated = True
            else:
                multiplier = self.params.output_multiplier.get(p.tier or "", 1.0)
                tin, tout = tokens_in, round(tokens_out * multiplier)
            if cap:
                tout = min(tout, cap)  # 单次输出不会超过该步骤的 max_tokens
            if repeat > 1:
                self.calibrated = True
            tin_total += round(tin * repeat)
            tout_total += round(tout * repeat)
            cost += estimate_cost(p.price, tin, tout) * repeat
            upper += estimate_cost(p.price, tin, cap or tout)
        return tin_total, tout_total, cost, upper


def estimate_pipeline(
    pipeline: Sequence[str],
    *,
    member_prices: Sequence[Price] = (),
    coordinator_price: Price | None = None,
    question_tokens: int,
    answer_tokens: int,
    revise_rounds: int,
    params: EstimateParams,
    reviews_per_answer: int | None = None,
    members: Sequence[Participant] | None = None,
    coordinator: Participant | None = None,
    history: EstimateHistory | None = None,
    caps: dict[str, int] | None = None,
) -> CostEstimate:
    """按步骤估算。n = 组员数，A = 答案长度，Q = 题目长度，O = 每次调用的固定开销，
    k = 每份答案的评审人数（每位评审者也评 k 份；None 表示全员互评）。

    有历史记录时，用该模型在该步骤的真实 token 中位数（含推理 token）代替公式，并按历史上
    每个座位的平均调用次数（格式重试、打回重做）放大；没有历史时，输出按档位乘以
    output_multiplier（推理模型的思考 token 也按输出计费）。caps 为各步骤的 max_tokens，用于上限。
    """
    if members is None:
        members = [Participant("", None, p) for p in member_prices]
    if coordinator is None and coordinator_price is not None:
        coordinator = Participant("", None, coordinator_price)
    pricer = _Pricer(params, history, dict(caps or {}))
    n = len(members)
    k = n - 1 if reviews_per_answer is None else max(0, min(reviews_per_answer, n - 1))
    o, q, a = params.prompt_overhead_tokens, question_tokens, answer_tokens
    r = params.review_tokens_per_peer
    revised = a + params.revise_overhead_tokens
    d = params.decompose_tokens
    coord = [coordinator] if coordinator else []
    rounds = {"review": revise_rounds, "revise": revise_rounds}
    # 步骤 → (参与者, 单次输入, 单次输出)
    shapes = {
        "answer": (members, o + q, a),
        "review": (members, o + q + k * a, k * r),
        "revise": (members, o + q + a + k * r, revised),
        "synthesize": (coord, o + q + n * revised, params.synthesize_tokens),
        # 协同模式：D = 拆分结果长度；每位成员按完成一块、每块按整题长度粗估（偏保守）
        "decompose": (coord, o + q, d),
        "volunteer": (members, o + q + d, params.volunteer_tokens),
        "assign": (coord, o + q + d + n * params.volunteer_tokens, params.assign_tokens),
        "work": (members, o + q + d + a, a),
        "cross_review": (members, o + q + k * a, k * r),
        "rework": (members, o + q + d + a + k * r, revised),
        "merge": (coord, o + q + d + n * revised, params.merge_tokens),
    }
    steps: list[StepCost] = []
    unknown: list[str] = []
    for step in pipeline:
        if step == "reveal":
            steps.append(StepCost(step, 0, 0, 0.0))
            continue
        if step not in shapes:
            unknown.append(step)
            continue
        people, tin, tout = shapes[step]
        times = rounds.get(step, 1)
        ti, to, cost, upper = pricer.calls(step, people, tin, tout)
        steps.append(StepCost(step, ti * times, to * times, cost * times, upper * times))

    return CostEstimate(
        total_usd=sum(s.cost_usd for s in steps),
        input_tokens=sum(s.input_tokens for s in steps),
        output_tokens=sum(s.output_tokens for s in steps),
        steps=tuple(steps),
        unknown_steps=tuple(unknown),
        max_usd=sum(s.max_usd for s in steps),
        calibrated=pricer.calibrated,
    )


def average_price(prices: Sequence[Price]) -> Price:
    if not prices:
        raise ValueError("没有可用于估价的模型")
    k = len(prices)
    return Price(
        input=sum(p.input for p in prices) / k,
        output=sum(p.output for p in prices) / k,
    )
