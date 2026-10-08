"""座位与代号：随机分配代号、互评分配（排除自评）、乱序。"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence


def assign_codes(
    model_ids: Sequence[str], codes: Sequence[str], rng: random.Random
) -> dict[str, str]:
    """随机把代号分给模型。返回 代号 → 模型 id，按代号池顺序排列（甲、乙、丙…）。

    代号顺序固定、模型随机，因此"组员甲"不会固定对应某一个模型。
    """
    if len(set(model_ids)) != len(model_ids):
        raise ValueError("模型 id 不能重复")
    if len(model_ids) > len(codes):
        raise ValueError(f"代号池只有 {len(codes)} 个，不够 {len(model_ids)} 个座位")
    shuffled = list(model_ids)
    rng.shuffle(shuffled)
    return dict(zip(codes[: len(shuffled)], shuffled, strict=True))


def review_assignments(codes: Sequence[str], rng: random.Random) -> dict[str, list[str]]:
    """每个评审者要评审的对象：除自己以外的所有人，顺序对每个评审者独立打乱。"""
    if len(set(codes)) != len(codes):
        raise ValueError("代号不能重复")
    result = {}
    for reviewer in codes:
        targets = [c for c in codes if c != reviewer]
        rng.shuffle(targets)
        result[reviewer] = targets
    return result


def reviews_for(
    target: str, reviews: Mapping[str, Mapping[str, object]], rng: random.Random
) -> list[tuple[str, object]]:
    """某个组员收到的评审（评审者 → 对该组员的评审），排除自评，随机排序。

    reviews: 评审者代号 → {被评审者代号 → 评审内容}
    """
    received = [
        (reviewer, by_target[target])
        for reviewer, by_target in reviews.items()
        if reviewer != target and target in by_target
    ]
    rng.shuffle(received)
    return received


def shuffled(items: Sequence[str], rng: random.Random) -> list[str]:
    """返回打乱后的副本（例如统筹看到的答案顺序）。"""
    out = list(items)
    rng.shuffle(out)
    return out
