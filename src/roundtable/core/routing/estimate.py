"""执行前的花费预估。token 数是粗估，参数在 routing.yaml 的 estimate 段，可按实际记录调整。"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from roundtable.core.config import Price
from roundtable.core.config.schema import EstimateParams
from roundtable.core.providers import estimate_cost

# 不在此表中的步骤（例如以后新增的插件）不计费，并在结果中列出
KNOWN_STEPS = frozenset({"answer", "review", "revise", "synthesize", "reveal"})


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


@dataclass(frozen=True)
class CostEstimate:
    total_usd: float
    input_tokens: int
    output_tokens: int
    steps: tuple[StepCost, ...]
    unknown_steps: tuple[str, ...] = field(default_factory=tuple)


def _calls(prices: Sequence[Price], tokens_in: int, tokens_out: int) -> tuple[int, int, float]:
    cost = sum(estimate_cost(p, tokens_in, tokens_out) for p in prices)
    return tokens_in * len(prices), tokens_out * len(prices), cost


def estimate_pipeline(
    pipeline: Sequence[str],
    *,
    member_prices: Sequence[Price],
    coordinator_price: Price | None,
    question_tokens: int,
    answer_tokens: int,
    revise_rounds: int,
    params: EstimateParams,
    reviews_per_answer: int | None = None,
) -> CostEstimate:
    """按步骤估算。n = 组员数，A = 答案长度，Q = 题目长度，O = 每次调用的固定开销，
    k = 每份答案的评审人数（每位评审者也评 k 份；None 表示全员互评）。"""
    n = len(member_prices)
    k = n - 1 if reviews_per_answer is None else max(0, min(reviews_per_answer, n - 1))
    o, q, a = params.prompt_overhead_tokens, question_tokens, answer_tokens
    r = params.review_tokens_per_peer
    revised = a + params.revise_overhead_tokens
    steps: list[StepCost] = []
    unknown: list[str] = []

    for step in pipeline:
        if step == "answer":
            tin, tout, cost = _calls(member_prices, o + q, a)
        elif step == "review":
            tin, tout, cost = _calls(member_prices, o + q + k * a, k * r)
            tin, tout, cost = tin * revise_rounds, tout * revise_rounds, cost * revise_rounds
        elif step == "revise":
            tin, tout, cost = _calls(member_prices, o + q + a + k * r, revised)
            tin, tout, cost = tin * revise_rounds, tout * revise_rounds, cost * revise_rounds
        elif step == "synthesize":
            prices = [coordinator_price] if coordinator_price else []
            tin, tout, cost = _calls(prices, o + q + n * revised, params.synthesize_tokens)
        elif step == "reveal":
            tin, tout, cost = 0, 0, 0.0
        else:
            unknown.append(step)
            continue
        steps.append(StepCost(step, tin, tout, cost))

    return CostEstimate(
        total_usd=sum(s.cost_usd for s in steps),
        input_tokens=sum(s.input_tokens for s in steps),
        output_tokens=sum(s.output_tokens for s in steps),
        steps=tuple(steps),
        unknown_steps=tuple(unknown),
    )


def average_price(prices: Sequence[Price]) -> Price:
    if not prices:
        raise ValueError("没有可用于估价的模型")
    k = len(prices)
    return Price(
        input=sum(p.input for p in prices) / k,
        output=sum(p.output for p in prices) / k,
    )
