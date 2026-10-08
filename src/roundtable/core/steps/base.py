"""步骤插件的公共部分：Step 协议与注册表、单桌上下文、调用与记录、渲染、状态恢复。"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar

from pydantic import ValidationError

from roundtable.core.allocation import IdentityScrubber
from roundtable.core.config import AppConfig
from roundtable.core.jsonout import JSONOutputError
from roundtable.core.prompts import PromptLibrary, RenderedPrompt
from roundtable.core.providers import (
    AllChannelsFailed,
    ChannelRouter,
    Completion,
    NoChannelAvailable,
)
from roundtable.core.routing import Question
from roundtable.core.storage import Repository

from .schemas import CheckedReview, EffortRecord, Revision, Synthesis, TableState

log = logging.getLogger(__name__)

ATTEMPTS = 2  # 输出格式不对时重试一次
T = TypeVar("T")


class StepFailed(RuntimeError):
    """步骤无法继续（例如统筹调用失败），需要编排引擎处理。"""


# --- 事件（供界面实时显示；只含代号，不含模型身份） --------------------------------


@dataclass(frozen=True)
class Event:
    type: str  # step_started / call_done / call_failed / member_dropped / step_finished …
    step: str
    table_no: int
    code: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[Event], None]


# --- 上下文 -------------------------------------------------------------------


@dataclass
class TableContext:
    session_id: str
    table_no: int
    question: Question
    members: dict[str, str]  # 代号 → 模型 id（代号顺序即座位顺序）
    coordinator: str | None
    config: AppConfig
    router: ChannelRouter
    prompts: PromptLibrary
    repo: Repository
    scrubber: IdentityScrubber
    rng: random.Random
    state: TableState = field(default_factory=TableState)
    on_event: EventSink | None = None
    # 步骤 → 提示词角色（来自方案的 prompt_roles；未指定时与步骤同名）
    prompt_roles: dict[str, str] = field(default_factory=dict)
    # 预估的答案长度（token），用于实质内容检查的字数下限
    expected_answer_tokens: int | None = None

    @property
    def effort_rule(self):
        return self.config.roundtable.effort_check

    # 代号与展示标签（"甲" ↔ "组员甲"）
    def label(self, code: str) -> str:
        return f"{self.config.personas.code_prefix}{code}"

    def to_code(self, text: str) -> str | None:
        t = text.strip()
        prefix = self.config.personas.code_prefix
        if t.startswith(prefix):
            t = t[len(prefix) :].strip()
        return t if t in self.members else None

    @property
    def active(self) -> list[str]:
        """尚未退出的组员代号（保持座位顺序）。"""
        return [c for c in self.members if c not in self.state.dropped]

    def emit(self, type_: str, step: str, code: str | None = None, **data: Any) -> None:
        if self.on_event:
            self.on_event(Event(type_, step, self.table_no, code, data))

    def scrub(self, text: str) -> str:
        """转给其他模型之前遮蔽身份；题目中出现的名称保留。"""
        return self.scrubber.scrub(text, self.question.text)

    def prompt_version(self, role: str) -> str:
        return self.config.roundtable.prompts[role]

    def prompt_role(self, step: str) -> str:
        return self.prompt_roles.get(step, step)

    def render(self, step: str, **values: str) -> RenderedPrompt:
        """按方案指定的角色渲染提示词；只传入该模板声明的变量。"""
        role = self.prompt_role(step)
        template = self.prompts.get(role, self.prompt_version(role))
        return template.render(**{k: v for k, v in values.items() if k in template.variables})

    def step_params(self, step: str) -> dict[str, Any]:
        return dict(self.config.roundtable.step_params.get(step, {}))

    def drop(self, code: str, step: str, reason: str) -> None:
        if code in self.state.dropped:
            return
        self.state.dropped[code] = reason
        self.repo.save_output(
            self.session_id,
            table_no=self.table_no,
            step=step,
            kind="dropout",
            code=code,
            content={"reason": reason},
        )
        self.emit("member_dropped", step, code)


# --- 调用 ---------------------------------------------------------------------


@dataclass(frozen=True)
class CallOutcome:
    completion: Completion | None
    call_id: int
    error: str | None = None


async def call_model(
    ctx: TableContext,
    *,
    step: str,
    role: str,
    model_id: str,
    prompt: RenderedPrompt,
    code: str | None = None,
) -> CallOutcome:
    """调用一次并记录（成功或失败都记录）。渠道层已经负责重试和切换。"""
    try:
        completion = await ctx.router.complete(model_id, prompt.messages, ctx.step_params(step))
    except (AllChannelsFailed, NoChannelAvailable) as exc:
        call_id = ctx.repo.record_call(
            ctx.session_id,
            step=step,
            role=role,
            model_id=model_id,
            messages=prompt.messages,
            table_no=ctx.table_no,
            code=code,
            prompt=prompt,
            failure=exc,
        )
        ctx.emit("call_failed", step, code)
        return CallOutcome(None, call_id, "调用失败")
    call_id = ctx.repo.record_call(
        ctx.session_id,
        step=step,
        role=role,
        model_id=model_id,
        messages=prompt.messages,
        table_no=ctx.table_no,
        code=code,
        prompt=prompt,
        completion=completion,
    )
    ctx.emit("call_done", step, code, tokens=completion.output_tokens, cost=completion.cost_usd)
    return CallOutcome(completion, call_id)


@dataclass(frozen=True)
class Parsed:
    value: Any | None
    call_id: int | None
    failed_call: bool  # 渠道全部失败（不是格式问题）
    error: str | None = None
    raw_text: str | None = None  # 最后一次成功调用的原始输出（格式不符时可用于降级）


async def call_and_parse(
    ctx: TableContext,
    *,
    step: str,
    role: str,
    model_id: str,
    prompt: RenderedPrompt,
    parse: Callable[[str], T | None],
    code: str | None = None,
) -> Parsed:
    """调用并解析；解析失败（抛错或返回 None）时重试一次。"""
    error = None
    call_id = None
    raw = None
    for attempt in range(ATTEMPTS):
        outcome = await call_model(
            ctx, step=step, role=role, model_id=model_id, prompt=prompt, code=code
        )
        call_id = outcome.call_id
        if outcome.completion is None:
            return Parsed(None, call_id, True, outcome.error, raw)
        raw = outcome.completion.text
        try:
            value = parse(raw)
        except (JSONOutputError, ValidationError, ValueError) as exc:
            value, error = None, f"输出格式不符：{type(exc).__name__}"
        else:
            error = None if value is not None else "输出格式不符"
        if value is not None:
            return Parsed(value, call_id, False, None, raw)
        log.warning("步骤 %s 输出格式不符（第 %d 次）", step, attempt + 1)
    return Parsed(None, call_id, False, error, raw)


async def gather_members(
    ctx: TableContext, codes: Sequence[str], work: Callable[[str], Awaitable[T]]
) -> dict[str, T]:
    """对多个组员并发执行同一项工作（每人独立，互不可见）。"""
    results = await asyncio.gather(*(work(c) for c in codes))
    return dict(zip(codes, results, strict=True))


# --- 渲染 ---------------------------------------------------------------------


def neutralize(text: str, tags: Sequence[str]) -> str:
    """防止内容中出现的结束标签提前闭合外层标签（提示注入）。"""
    for tag in tags:
        text = text.replace(f"</{tag}", f"<​/{tag}")
    return text


TAGS = ("answer", "review", "question", "your_answer")


def answer_block(label: str, text: str, *, flagged: bool = False) -> str:
    """flagged：该组员的产出被标记为敷衍（统筹汇总时会看到这个属性）。"""
    attr = ' flagged="未通过实质内容检查"' if flagged else ""
    return f'<answer code="{label}"{attr}>\n{neutralize(text, TAGS)}\n</answer>'


def review_block(label: str, review: CheckedReview) -> str:
    lines = []
    for n, issue in enumerate(review.issues, 1):
        lines += [
            f"问题 {n}（{issue.severity}）",
            f"位置：{issue.location}",
            f"问题：{issue.problem}",
            f"建议：{issue.suggestion}",
        ]
    if review.checked:
        lines.append(f"已检查：{review.checked}")
    if review.strengths:
        lines.append(f"值得保留：{review.strengths}")
    body = neutralize("\n".join(lines), TAGS)
    return f'<review from="{label}" verdict="{review.verdict}">\n{body}\n</review>'


# --- Step 协议与注册表 ----------------------------------------------------------


@dataclass(frozen=True)
class StepResult:
    step: str
    calls: int = 0
    dropped: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()  # 降级的代号（统筹为 "coordinator"）
    notes: tuple[str, ...] = ()


class Step(Protocol):
    name: str

    async def run(self, ctx: TableContext) -> StepResult: ...


_STEPS: dict[str, Step] = {}


def register_step(step: Step) -> Step:
    if step.name in _STEPS:
        raise ValueError(f"步骤 {step.name!r} 已注册")
    _STEPS[step.name] = step
    return step


def get_step(name: str) -> Step:
    try:
        return _STEPS[name]
    except KeyError:
        raise KeyError(f"未注册的步骤 {name!r}；已注册：{sorted(_STEPS)}") from None


def step_names() -> list[str]:
    return sorted(_STEPS)


def check_pipelines(config: AppConfig) -> None:
    """配置中出现的所有步骤（默认流程和各方案的流程）都必须已注册。"""
    from roundtable.core.config import ConfigError

    pipelines = {
        "roundtable.pipeline": config.roundtable.pipeline,
        "roundtable.collab_pipeline": config.roundtable.collab_pipeline,
    }
    for name, plan in config.routing.plans.items():
        if plan.pipeline is not None:
            pipelines[f"routing.plans.{name}.pipeline"] = plan.pipeline
    problems = [
        f"{where} 中的 {step!r}"
        for where, steps in pipelines.items()
        for step in steps
        if step not in _STEPS
    ]
    if problems:
        raise ConfigError(f"未注册的步骤：{problems}；已注册：{step_names()}")


# --- 从数据库恢复 ----------------------------------------------------------------


def restore_state(repo: Repository, session_id: str, table_no: int) -> TableState:
    """根据已保存的产出重建单桌状态（暂停后恢复时使用）。"""
    state = TableState()
    for o in repo.outputs(session_id, table_no=table_no):
        kind, code = o["kind"], o["code"]
        if kind == "answer":
            state.answers[code] = o["content"]
        elif kind == "review":
            data = json.loads(o["content"])
            state.reviews[code] = tuple(CheckedReview.from_dict(r) for r in data["reviews"])
        elif kind == "revision":
            state.revisions[code] = Revision.from_dict(json.loads(o["content"]))
        elif kind == "synthesis":
            state.synthesis = Synthesis.from_dict(json.loads(o["content"]))
        elif kind == "dropout":
            state.dropped[code] = json.loads(o["content"])["reason"]
        elif kind == "effort":
            record = EffortRecord.from_dict(o["step"], code, json.loads(o["content"]))
            state.effort[record.key] = record
        else:
            restore_collab(state, kind, code, json.loads(o["content"]))
    return state


def restore_collab(state: TableState, kind: str, code: str | None, data: dict[str, Any]) -> None:
    """协同模式各步骤的产出。"""
    from .collab_schemas import Assignment, Merge, Subtask, Volunteer

    c = state.collab
    if kind == "subtasks":
        c.subtasks = tuple(Subtask.from_dict(x) for x in data["subtasks"])
        c.subtasks_degraded = data.get("degraded", False)
    elif kind == "volunteer":
        c.volunteers[code] = Volunteer.from_dict(data)
    elif kind == "assignment":
        c.assignment = Assignment.from_dict(data)
    elif kind == "work":
        c.works[(data["subtask"], code)] = data["text"]
    elif kind == "cross_review":
        c.cross_reviews[code] = tuple(CheckedReview.from_dict(r) for r in data["reviews"])
    elif kind == "rework":
        c.reworks[(data["subtask"], code)] = Revision.from_dict(data)
    elif kind == "merge":
        c.merge = Merge.from_dict(data)


def members_from_seats(seats: Sequence[Mapping[str, Any]]) -> tuple[dict[str, str], str | None]:
    members = {s["code"]: s["model_id"] for s in seats if s["role"] == "member"}
    coordinator = next((s["model_id"] for s in seats if s["role"] == "coordinator"), None)
    return members, coordinator
