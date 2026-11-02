"""媒体模型的选择：只看能力标签和档位，同档内随机，不针对任何品牌。
图片另按出图顺序排（本地 → 不花钱 → 按价，见 draw_order.py）。"""

from __future__ import annotations

import random
from collections.abc import Iterable

from roundtable.core.config import ModelSpec
from roundtable.core.providers import ChannelRouter

from .draw_order import draw_key, rank_draw_models
from .pricing import KIND_TAGS


def media_group(router: ChannelRouter, kind: str, tier: str) -> list[ModelSpec]:
    """某种媒体在指定档位下的候选模型（可用的、不上桌、带对应能力标签）：先取指定档位；
    没有时取未分档的，再没有取另一档。保持配置顺序；图片按出图顺序排、去掉手动关掉的。"""
    tag = KIND_TAGS[kind]
    pool = [m for m in router.available_models() if not m.seat and tag in m.tags]
    if kind == "image":
        pool = rank_draw_models(router, pool)
    for group in (
        [m for m in pool if m.tier == tier],
        [m for m in pool if m.tier is None],
        pool,
    ):
        if group:
            return group
    return []


def pick_media_model(
    router: ChannelRouter,
    kind: str,
    tier: str,
    rng: random.Random,
    prefer_tags: Iterable[str] = (),
) -> ModelSpec | None:
    """从候选组里选一个，不看 id、厂商（换名不影响）：先限定在带偏好标签的模型里（有的话），
    再限定在 default: true 的模型里（有的话），最后随机。图片只在出图顺序最靠前的那几个里随机
    （本地 / 不花钱的先用）。没有可用模型时返回 None。"""
    group = media_group(router, kind, tier)
    if not group:
        return None
    wanted = set(prefer_tags)
    group = [m for m in group if wanted & set(m.tags)] or group
    group = [m for m in group if m.default] or group
    if kind == "image":
        best = min(draw_key(router, m) for m in group)
        group = [m for m in group if draw_key(router, m) == best]
    return rng.choice(group)


def pick_stt_model(router: ChannelRouter, rng: random.Random) -> ModelSpec | None:
    """语音转文字：优先最便宜档的 stt 模型。"""
    pool = [m for m in router.available_models() if not m.seat and "stt" in m.tags]
    for tier in ("budget", "flagship", None):
        group = [m for m in pool if m.tier == tier]
        if group:
            return rng.choice(group)
    return None
