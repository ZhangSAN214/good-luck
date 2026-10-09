"""按配置价格预估费用。实际费用优先使用渠道返回的数值。"""

from __future__ import annotations

from roundtable.core.config import ModelSpec, Price, Route

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


DEFAULT_PROMPT_TOKENS = 300  # 图像提示词的典型输入 token 数


def image_cost(
    model: ModelSpec,
    route: Route | None,
    images: int,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    """渠道没有返回实际费用时，一次图像生成的费用：每张单价（media_price）优先；
    否则按 token：用量（output_tokens）优先，没有用量时按 image_tokens × 张数估计。"""
    price = model.media_price_for(route) if route else model.media_price
    if price is not None and price.unit == "image":
        return price.usd * images
    tokens = output_tokens or (model.image_tokens or 0) * images
    token_price = model.price_for(route) if route else model.price
    return estimate_cost(token_price, input_tokens or DEFAULT_PROMPT_TOKENS, tokens)
