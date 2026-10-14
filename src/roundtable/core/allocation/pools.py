"""候选池与随机抽取。只依据档位、能力标签和价格，不针对任何具体模型或厂商。"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence

from roundtable.core.config import ModelSpec
from roundtable.core.config.schema import Tier


class NotEnoughModels(Exception):
    """满足条件的可用模型不够。"""


def eligible(
    models: Iterable[ModelSpec],
    *,
    tiers: Iterable[Tier] | None = None,
    required_tags: Iterable[str] = (),
    exclude: Iterable[str] = (),
) -> list[ModelSpec]:
    """按档位、必需标签筛选，并排除指定 id。保持输入顺序。"""
    tier_set = set(tiers) if tiers is not None else None
    required, excluded = set(required_tags), set(exclude)
    return [
        m
        for m in models
        if m.id not in excluded
        and (tier_set is None or m.tier in tier_set)
        and required <= set(m.tags)
    ]


def prefer_tags(candidates: Sequence[ModelSpec], tags: Iterable[str], k: int) -> list[ModelSpec]:
    """如果带有任一偏好标签的候选不少于 k 个，只保留它们；否则不筛选。"""
    wanted = set(tags)
    if not wanted:
        return list(candidates)
    tagged = [m for m in candidates if wanted & set(m.tags)]
    return tagged if len(tagged) >= k else list(candidates)


def draw(
    candidates: Sequence[ModelSpec],
    k: int,
    rng: random.Random,
    *,
    distinct_vendors: bool = True,
    avoid_vendors: Iterable[str] = (),
) -> list[ModelSpec]:
    """从候选中随机抽 k 个。

    distinct_vendors：尽量让抽中的模型来自不同厂商（也尽量避开 avoid_vendors），
    不够时再放宽。随机性由先整体打乱保证：同等条件下每个候选被选中的概率相同。
    """
    if k < 0 or k > len(candidates):
        raise NotEnoughModels(f"需要 {k} 个模型，只有 {len(candidates)} 个符合条件")
    pool = list(candidates)
    rng.shuffle(pool)
    if not distinct_vendors:
        return pool[:k]

    picked: list[ModelSpec] = []
    seen = set(avoid_vendors)
    # 第一轮：每个厂商最多一个，且避开 avoid_vendors
    for m in pool:
        if len(picked) == k:
            break
        if m.vendor not in seen:
            picked.append(m)
            seen.add(m.vendor)
    # 第二轮：放宽，按打乱后的顺序补足
    for m in pool:
        if len(picked) == k:
            break
        if m not in picked:
            picked.append(m)
    return picked


def cheapest(
    candidates: Sequence[ModelSpec],
    rng: random.Random,
    *,
    input_tokens: int,
    output_tokens: int,
) -> ModelSpec:
    """按预计调用成本选最便宜的模型；价格相同的随机选一个。"""
    if not candidates:
        raise NotEnoughModels("没有符合条件的模型")

    def cost(m: ModelSpec) -> float:
        return m.price.input * input_tokens + m.price.output * output_tokens

    lowest = min(cost(m) for m in candidates)
    ties = [m for m in candidates if cost(m) == lowest]
    return rng.choice(ties)
