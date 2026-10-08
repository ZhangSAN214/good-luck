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


def review_assignments(
    codes: Sequence[str], rng: random.Random, per_answer: int | None = None
) -> dict[str, list[str]]:
    """每个评审者要评审的对象（永远不含自己），顺序对每个评审者独立打乱。

    per_answer 为空或不小于 n - 1 时全员互评；否则把代号随机排成一圈，
    每人评审圈上紧随其后的 per_answer 个人：每份答案恰好被 per_answer 人评审，
    每人也恰好评审 per_answer 份，负担均衡。
    """
    if len(set(codes)) != len(codes):
        raise ValueError("代号不能重复")
    if per_answer is not None and per_answer < 1:
        raise ValueError("per_answer 至少为 1")
    n = len(codes)
    ring = list(codes)
    rng.shuffle(ring)
    k = n - 1 if per_answer is None else min(per_answer, n - 1)
    result = {}
    for i, reviewer in enumerate(ring):
        targets = [ring[(i + j) % n] for j in range(1, k + 1)]
        rng.shuffle(targets)
        result[reviewer] = targets
    return {c: result[c] for c in codes}


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
