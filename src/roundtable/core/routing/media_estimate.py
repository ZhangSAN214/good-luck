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
    # 平均按"通常会选到的"模型（有默认模型时只算默认的）；上限按候选里最贵的
    typical = [m for m in group if m.default] or group
    costs = [estimate_generation(m, kind, rules, chars=chars) for m in typical]
    worst = max(estimate_generation(m, kind, rules, chars=chars) for m in group)
    mean = sum(costs) / len(costs)

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


def style_wanted(
    config: AppConfig,
    router: ChannelRouter,
    *,
    has_style_images: bool,
    media: str | None,
    collab: bool,
    tier: str | None,
) -> bool:
    """要不要提取风格规范：有风格参考图，而且会用到画图（讨论模式选了图片输出；
    协同模式且有可用的画图模型，由统筹决定哪些子任务生成图片）。"""
    if not (
        config.roundtable.style.enabled and config.roundtable.media.enabled and has_style_images
    ):
        return False
    if not collab:
        return media == "image"
    return bool(media_group(router, "image", tier or config.roundtable.media.default_tier))


def style_step_cost(
    config: AppConfig,
    *,
    members: Sequence[Participant],
    coordinator: Participant | None,
    by_id: Mapping[str, ModelSpec],
    question_tokens: int,
) -> StepCost:
    """style 步骤：带 vision 标签的成员（最多 reviewers 位）各看一遍原图 + 统筹合并一次。"""
    rules = config.roundtable.style
    params = config.routing.estimate
    out_cap = int(config.roundtable.step_params.get("style", {}).get("max_tokens", 1500))
    seeing = [p for p in members if "vision" in by_id[p.model_id].tags][: rules.reviewers]
    tin = params.prompt_overhead_tokens + question_tokens + params.image_tokens
    cost = sum(estimate_cost(p.price, tin, out_cap // 2) for p in seeing)
    upper = sum(estimate_cost(p.price, tin, out_cap) for p in seeing)
    if coordinator is not None:
        merge_in = params.prompt_overhead_tokens + question_tokens + max(1, len(seeing)) * out_cap
        cost += estimate_cost(coordinator.price, merge_in // 2, out_cap // 2)
        upper += estimate_cost(coordinator.price, merge_in, out_cap)
    return StepCost("style", len(seeing) + 1, len(seeing) + 1, cost, upper)


def with_style_step(estimate: CostEstimate, **kw) -> CostEstimate:
    """把 style 步骤的花费并入估算（放在最前面）。"""
    step = style_step_cost(kw.pop("config"), **kw)
    return replace(
        estimate,
        total_usd=estimate.total_usd + step.cost_usd,
        max_usd=estimate.max_usd + step.max_usd,
        steps=(step, *estimate.steps),
    )
