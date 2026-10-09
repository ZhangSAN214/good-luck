"""媒体输出的花费预估：生成费用（取候选模型的平均与最大值）+ 评审与改写提示词的模型调用。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from roundtable.core.config import AppConfig, ModelSpec
from roundtable.core.media import estimate_generation, media_group
from roundtable.core.providers import ChannelRouter, estimate_cost

from .estimate import CostEstimate, Participant, StepCost


def media_step_cost(
    config: AppConfig,
    router: ChannelRouter,
    *,
    kind: str,
    tier: str,
    members: Sequence[Participant],
    coordinator: Participant | None,
    by_id: Mapping[str, ModelSpec],
    question_tokens: int,
    prompt_tokens: int,
) -> StepCost:
    """讨论模式 media 步骤：预计按 expected_rounds 轮生成；上限按 max_rounds 轮、每轮都评审。"""
    rules = config.roundtable.media
    params = config.routing.estimate
    group = media_group(router, kind, tier)
    if not group:
        return StepCost("media", 0, 0, 0.0, 0.0)
    chars = prompt_tokens * 2
    costs = [estimate_generation(m, kind, rules, chars=chars) for m in group]
    mean, worst = sum(costs) / len(costs), max(costs)

    # 评审（带 vision 标签的成员，最多 reviewers 位）+ 统筹改写提示词；语音没有评审
    seeing = [p for p in members if "vision" in by_id[p.model_id].tags][: rules.reviewers]
    if kind == "speech":
        review = 0.0
    else:
        pictures = {"image": rules.images_per_round, "video": rules.video.frames}[kind]
        tin = params.prompt_overhead_tokens + question_tokens + prompt_tokens
        tin += pictures * params.image_tokens
        review = sum(estimate_cost(p.price, tin, rules.review_tokens) for p in seeing)
        if coordinator is not None and seeing:
            refine_in = params.prompt_overhead_tokens + prompt_tokens + len(seeing) * 300
            review += estimate_cost(coordinator.price, refine_in, rules.refine_tokens)
    rounds = 1.0 if kind == "speech" else rules.expected_rounds
    max_rounds = 1 if kind == "speech" else rules.max_rounds
    expected = mean * rounds + review * rounds
    upper = worst * max_rounds + review * max(0, max_rounds - 1)
    return StepCost("media", 0, 0, expected, upper)


def with_media_step(estimate: CostEstimate, **kw) -> CostEstimate:
    """把 media 步骤的花费并入估算（放在 reveal 之前）。"""
    config, router = kw.pop("config"), kw.pop("router")
    step = media_step_cost(config, router, **kw)
    steps = list(estimate.steps)
    index = next((i for i, s in enumerate(steps) if s.step == "reveal"), len(steps))
    steps.insert(index, step)
    return replace(
        estimate,
        total_usd=estimate.total_usd + step.cost_usd,
        max_usd=estimate.max_usd + step.max_usd,
        steps=tuple(steps),
    )
