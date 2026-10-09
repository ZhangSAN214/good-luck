"""规划员：规则判断不出难度时，调用最便宜的可用模型判断题型、难度和答案长度。"""

from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from roundtable.core.allocation import cheapest, eligible
from roundtable.core.config import AppConfig, ModelSpec
from roundtable.core.config.schema import Difficulty
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import (
    AllChannelsFailed,
    ChannelRouter,
    Completion,
    Message,
    NoChannelAvailable,
)

log = logging.getLogger(__name__)

ATTEMPTS = 2  # 解析失败时重试一次
OTHER = "other"


class PlannerOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    task_type: str = OTHER
    difficulty: Difficulty
    expected_answer_tokens: int | None = Field(default=None, gt=0)
    reason: str = ""


@dataclass(frozen=True)
class PlannerResult:
    model_id: str | None
    output: PlannerOutput | None
    cost_usd: float
    calls: int
    prompt_version: str
    prompt_sha256: str | None
    error: str | None = None
    # 供存储逐次记录：发送的消息、每次成功调用的结果
    messages: tuple[Message, ...] = ()
    completions: tuple[Completion, ...] = ()

    @property
    def ok(self) -> bool:
        return self.output is not None


def pick_planner_model(
    config: AppConfig, available: Sequence[ModelSpec], rng: random.Random
) -> ModelSpec | None:
    spec = config.routing.planner
    pool = eligible(available, tiers=spec.tiers)
    if not pool:
        return None
    return cheapest(
        pool,
        rng,
        input_tokens=spec.expected_input_tokens,
        output_tokens=spec.expected_output_tokens,
    )


async def run_planner(
    question: str,
    *,
    config: AppConfig,
    router: ChannelRouter,
    prompts: PromptLibrary,
    available: Sequence[ModelSpec],
    rng: random.Random,
) -> PlannerResult:
    version = config.roundtable.prompts["planner"]
    model = pick_planner_model(config, available, rng)
    if model is None:
        return PlannerResult(None, None, 0.0, 0, version, None, "没有可用的规划员模型")

    vocab = [t for t in config.models.tag_vocabulary]
    rendered = prompts.render(
        "planner", version, question=question, task_types=", ".join([*vocab, OTHER])
    )
    params = {"max_tokens": config.routing.planner.max_tokens, "reasoning": {"effort": "low"}}
    cost, error = 0.0, None
    completions: list[Completion] = []

    def result(output: PlannerOutput | None) -> PlannerResult:
        return PlannerResult(
            model.id,
            output,
            cost,
            len(completions),
            version,
            rendered.sha256,
            None if output else error,
            rendered.messages,
            tuple(completions),
        )

    messages = list(rendered.messages)
    for _ in range(ATTEMPTS):
        try:
            completion = await router.complete(model.id, messages, params)
        except (AllChannelsFailed, NoChannelAvailable) as exc:
            error = f"调用失败：{exc}"
            break  # 渠道层已经重试和切换过
        completions.append(completion)
        cost += completion.cost_usd
        try:
            output = PlannerOutput.model_validate(extract_json_object(completion.text))
        except (JSONOutputError, ValidationError) as exc:
            error = f"输出无法解析：{exc}"[:200]
            log.warning("规划员输出无法解析，第 %d 次", len(completions))
            # 重试时附上上一次的输出与具体原因，不原样重发
            messages = [
                *rendered.messages,
                Message("assistant", completion.text.strip()[:1500] or "（空）"),
                Message(
                    "user",
                    f"你上一次的输出无法使用：{error}。"
                    "请严格按要求的 JSON 格式重新输出，不要输出其他文字。",
                ),
            ]
            continue
        if output.task_type not in vocab:
            output = output.model_copy(update={"task_type": OTHER})
        return result(output)
    return result(None)
