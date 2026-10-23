"""分诊小组（R15）：题目开始时，几位最便宜的模型各给一条简短建议（用哪种方式作答、难度），
由代码汇总成一个推荐。只推荐，不执行：是否采用由用户在确认卡片上决定。

省钱手段：规则能判断为简单题时不调用模型；每次调用的输出只有一小段 JSON；
票数相同时选更便宜的方式。选人只看档位和价格；同一份提示词、同样的参数发给每位成员；
卡片里不出现任何模型名。
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

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

from .triage import Question, triage

log = logging.getLogger(__name__)

# 由便宜到贵：票数相同时取靠前的（头脑风暴·节电约 $0.07，比讨论便宜）
PANEL_WORKFLOWS = ("solo", "brainstorm", "discussion", "collab")
DIFFICULTIES = ("simple", "medium", "hard")
PanelWorkflow = Literal["solo", "brainstorm", "discussion", "collab"]


class PanelVote(BaseModel):
    model_config = ConfigDict(extra="ignore")

    difficulty: Difficulty
    workflow: PanelWorkflow
    reason: str = Field(default="", max_length=200)


@dataclass(frozen=True)
class PanelCall:
    """一次成功的调用，供逐次记录。"""

    model_id: str
    messages: tuple[Message, ...]
    completion: Completion


@dataclass(frozen=True)
class PanelResult:
    workflow: str
    tier: str
    difficulty: str
    source: Literal["rules", "panel", "default"]
    votes: tuple[PanelVote, ...] = ()
    cost_usd: float = 0.0
    calls: tuple[PanelCall, ...] = ()
    prompt_version: str = ""
    prompt_sha256: str | None = None
    note: str = ""

    @property
    def reasons(self) -> list[str]:
        seen: list[str] = []
        for v in self.votes:
            if v.reason and v.reason not in seen:
                seen.append(v.reason)
        return seen


def pick_panel(
    config: AppConfig, available: Sequence[ModelSpec], rng: random.Random
) -> list[ModelSpec]:
    """所选档位里预计调用成本最低的 size 个（价格相同随机）。只看档位和价格。"""
    spec = config.routing.panel
    pool = eligible(available, tiers=spec.tiers)
    chosen: list[ModelSpec] = []
    while pool and len(chosen) < spec.size:
        best = cheapest(
            pool,
            rng,
            input_tokens=spec.expected_input_tokens,
            output_tokens=spec.expected_output_tokens,
        )
        chosen.append(best)
        pool = [m for m in pool if m.id != best.id]
    return chosen


def aggregate(votes: Sequence[PanelVote], config: AppConfig) -> tuple[str, str, str]:
    """(作答方式, 档位, 难度)：方式取多数票（票数相同取更便宜的），难度取中位数。"""
    counts = Counter(v.workflow for v in votes)
    top = max(counts.values())
    workflow = next(w for w in PANEL_WORKFLOWS if counts.get(w) == top)
    ranks = sorted(DIFFICULTIES.index(v.difficulty) for v in votes)
    difficulty = DIFFICULTIES[ranks[len(ranks) // 2]]
    return workflow, config.routing.panel.tier_by_difficulty[difficulty], difficulty


def rules_verdict(question: Question, config: AppConfig) -> PanelResult | None:
    """规则就能判断为简单题时，不调用任何模型。"""
    spec = config.routing.panel
    if not spec.skip_when_simple:
        return None
    rules = triage(question, config.routing.triage)
    if rules.difficulty == "simple":
        return PanelResult(
            "solo",
            spec.tier_by_difficulty["simple"],
            "simple",
            "rules",
            note=f"规则 {rules.difficulty_rule} 判断为简单题，没有调用分诊小组",
        )
    return None


async def _ask(
    model: ModelSpec, config: AppConfig, router: ChannelRouter, messages: list[Message]
) -> tuple[PanelVote | None, PanelCall | None, float, str]:
    params = {"max_tokens": config.routing.panel.max_tokens, "reasoning": {"effort": "low"}}
    try:
        completion = await router.complete(model.id, messages, params)
    except (AllChannelsFailed, NoChannelAvailable) as exc:
        return None, None, 0.0, f"调用失败：{exc}"[:200]
    call = PanelCall(model.id, tuple(messages), completion)
    try:
        vote = PanelVote.model_validate(extract_json_object(completion.text))
    except (JSONOutputError, ValidationError) as exc:
        return None, call, completion.cost_usd, f"输出无法解析：{exc}"[:200]
    return vote, call, completion.cost_usd, ""


async def run_panel(
    question: Question,
    *,
    config: AppConfig,
    router: ChannelRouter,
    prompts: PromptLibrary,
    available: Sequence[ModelSpec],
    rng: random.Random,
) -> PanelResult:
    """小组建议。解析失败的成员不计票（不重试，省钱）；没人给出有效票时用默认建议。"""
    skipped = rules_verdict(question, config)
    if skipped is not None:
        return skipped
    spec = config.routing.panel
    default = PanelResult(
        config.routing.panel.default_workflow,
        spec.tier_by_difficulty[config.routing.default_difficulty],
        config.routing.default_difficulty,
        "default",
    )
    members = pick_panel(config, available, rng)
    version = config.roundtable.prompts["panel"]
    if not members:
        return _noted(default, version, None, "没有可用的分诊模型，使用默认建议")
    rendered = prompts.render("panel", version, question=question.text)
    messages = list(rendered.messages)  # 每位成员收到完全相同的请求（各发给不同的模型）
    answers = await asyncio.gather(*(_ask(m, config, router, messages) for m in members))
    votes = tuple(v for v, _, _, _ in answers if v is not None)
    calls = tuple(c for _, c, _, _ in answers if c is not None)
    cost = sum(c for _, _, c, _ in answers)
    if not votes:
        errors = "；".join(e for _, _, _, e in answers if e)
        return PanelResult(
            default.workflow, default.tier, default.difficulty, "default",
            cost_usd=cost, calls=calls, prompt_version=version, prompt_sha256=rendered.sha256,
            note=f"分诊小组没有给出有效建议，使用默认建议（{errors}）"[:300],
        )  # fmt: skip
    workflow, tier, difficulty = aggregate(votes, config)
    return PanelResult(
        workflow, tier, difficulty, "panel", votes, cost, calls, version, rendered.sha256
    )


def _noted(result: PanelResult, version: str, sha: str | None, note: str) -> PanelResult:
    return PanelResult(
        result.workflow, result.tier, result.difficulty, result.source,
        prompt_version=version, prompt_sha256=sha, note=note,
    )  # fmt: skip
