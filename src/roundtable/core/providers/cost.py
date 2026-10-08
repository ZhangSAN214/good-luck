"""按配置价格预估费用。实际费用优先使用渠道返回的数值。"""

from __future__ import annotations

from roundtable.core.config import Price

PER_MILLION = 1_000_000


def estimate_cost(
    price: Price, input_tokens: int, output_tokens: int, cached_tokens: int = 0
) -> float:
    """input_tokens 含缓存命中部分；cached_input 价格缺省时按 input 价计。"""
    cached = max(0, min(cached_tokens, input_tokens))
    cached_rate = price.input if price.cached_input is None else price.cached_input
    total = (
        (input_tokens - cached) * price.input + cached * cached_rate + output_tokens * price.output
    )
    return total / PER_MILLION
