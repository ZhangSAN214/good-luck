"""媒体模型的计价：按张 / 秒 / 分钟 / 字符，渠道返回实际费用时以实际为准。"""

from __future__ import annotations

from roundtable.core.config import MediaRules, ModelSpec, Route
from roundtable.core.providers import estimate_cost

KINDS = ("image", "speech", "video")
# 媒体种类 → 生成所需的模型能力标签
KIND_TAGS = {"image": "image_gen", "speech": "tts", "video": "video_gen"}
KIND_LABELS = {"image": "图片", "speech": "语音", "video": "视频"}


def quantities(*, images: int = 0, seconds: float = 0.0, chars: int = 0) -> dict[str, float]:
    return {"image": images, "second": seconds, "minute": seconds / 60, "char": chars}


def unit_cost(
    model: ModelSpec,
    route: Route | None = None,
    *,
    images: int = 0,
    seconds: float = 0.0,
    chars: int = 0,
) -> float | None:
    """按配置的 media_price 计算；没有 media_price 时返回 None。"""
    price = model.media_price_for(route) if route else model.media_price
    if price is None:
        return None
    return price.usd * quantities(images=images, seconds=seconds, chars=chars)[price.unit]


def estimate_generation(model: ModelSpec, kind: str, rules: MediaRules, *, chars: int = 0) -> float:
    """一次生成的预计费用。没有 media_price 的模型（按 token 计价）按一次典型调用估算。"""
    if kind == "image":
        cost = unit_cost(model, images=rules.images_per_round)
    elif kind == "video":
        cost = unit_cost(model, seconds=rules.video.duration_s)
    else:
        cost = unit_cost(model, chars=min(chars, rules.speech.max_chars))
    if cost is not None:
        return cost
    return estimate_cost(model.price, 300, 1500)
