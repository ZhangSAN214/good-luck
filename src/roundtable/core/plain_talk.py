"""说话底线（去八股化）：不用金句收尾、不喊格言口号、不排比升华、不写空洞总结。

两层：
1. 提示词：``plain_talk.roles`` 里的提示词每次调用都接上 ``plain_talk`` 提示词
   （system 段接在系统提示之后，user 段接在第一条用户消息之后）。同一步骤所有成员完全相同，
   聊天和工作模式共用，和人设无关。
2. 说话前检查（成员自我监控的雏形）：聊天、生活、朋友圈的发言发出前，按配置里的规则表
   用程序检查（不调用模型）；查出问题才用这位成员自己的聊天档（节电档）模型改写一次，
   改写失败、超预算或没改好就发原文。从不重试。

检查（``check``）是纯函数；改写通过可替换的 ``Rewriter`` 接入，以后由成员引擎接管时只需换掉它。
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from roundtable.core.attachments.model import append_to_messages
from roundtable.core.budget.guard import EPS, month_window
from roundtable.core.prompts import PromptLibrary, RenderedPrompt
from roundtable.core.providers import Message

ROLE = "plain_talk"
REWRITE_ROLE = "plain_rewrite"
REWRITE_STEP = "plain_rewrite"  # calls 表里改写调用的步骤名（统计每月花费）
TAGS = ("draft", "problems")

_CLAUSE_SPLIT = re.compile(r"[，,；;。.！!？?、…～~\n]+")
_SENTENCE_END = re.compile(r"[。.！!？?…～~\n]+")
# 句末的语气、表情符号等，取最后一句之前先去掉
_TRAILING = re.compile(r"[\s\W_]+$")


@dataclass(frozen=True)
class Hit:
    rule: str  # 规则名
    problem: str  # 给成员看的问题说明
    excerpt: str  # 命中的原文片段


# --- 第 1 层：提示词 ---------------------------------------------------------------


def applies(config: Any, role: str) -> bool:
    rules = config.roundtable.plain_talk
    return rules.enabled and role in rules.roles


def with_plain_talk(
    prompts: PromptLibrary, config: Any, messages: Sequence[Message], role: str
) -> tuple[Message, ...]:
    """给 role 的一次调用接上说话底线；role 不在配置的 roles 里时原样返回。"""
    if not applies(config, role):
        return tuple(messages)
    template = prompts.get(ROLE, config.roundtable.prompts[ROLE])
    return append_to_messages(messages, template)


def plain_prompt(prompts: PromptLibrary, config: Any, prompt: RenderedPrompt) -> RenderedPrompt:
    """同上，作用在渲染好的提示词上（版本号和哈希仍是原提示词的；实际消息随调用入库）。"""
    messages = with_plain_talk(prompts, config, prompt.messages, prompt.role)
    return prompt if messages == prompt.messages else replace(prompt, messages=messages)


# --- 第 2 层：说话前检查 -------------------------------------------------------------


def _clauses(text: str) -> list[str]:
    return [c.strip() for c in _CLAUSE_SPLIT.split(text) if c.strip()]


def last_sentence(text: str) -> str:
    sentences = [s.strip() for s in _SENTENCE_END.split(_TRAILING.sub("", text)) if s.strip()]
    return sentences[-1] if sentences else ""


def _parallel_run(text: str, min_run: int, heads: Sequence[str]) -> str | None:
    """连续 min_run 个以上开头相同的分句（排比）：返回这一串，没有时返回 None。"""

    def same(a: str, b: str) -> bool:
        if len(a) >= 4 and len(b) >= 4 and a[:2] == b[:2]:
            return True
        return a[0] in heads and a[0] == b[0] and min(len(a), len(b)) >= 2

    clauses = _clauses(text)
    start = 0
    for i in range(1, len(clauses) + 1):
        if i < len(clauses) and same(clauses[start], clauses[i]):
            continue
        if i - start >= min_run:
            return "，".join(clauses[start:i])
        start = i
    return None


def check(text: str, rules: Any) -> list[Hit]:
    """按规则表检查一条发言（rules：``plain_talk.check``）。纯函数，不调用模型。"""
    if len(text.strip()) < rules.min_chars:
        return []
    hits: list[Hit] = []
    ending = last_sentence(text)
    for rule in rules.rules:
        excerpt: str | None = None
        if rule.kind == "parallel":
            excerpt = _parallel_run(text, rule.min_run, rule.heads)
        else:
            target = ending if rule.kind == "ending" else text
            for pattern in rule.patterns:
                m = re.search(pattern, target)
                if m:
                    excerpt = ending if rule.kind == "ending" else m.group(0)
                    break
        if excerpt:
            hits.append(Hit(rule.name, rule.problem, excerpt[:60]))
    return hits


def describe(hits: Sequence[Hit]) -> str:
    return "\n".join(f"- {h.problem}（原文：{h.excerpt}）" for h in hits)


# 改写函数：(原文, 命中的问题) → 改好的文字；不改或失败时返回 None
Rewriter = Callable[[str, list[Hit]], Awaitable[str | None]]


@dataclass(frozen=True)
class Reviewed:
    text: str  # 最终发出的文字
    hits: tuple[Hit, ...]  # 原文查出的问题
    rewritten: bool


async def review(text: str, rules: Any, rewrite: Rewriter) -> Reviewed:
    """说话前检查：没问题原样发；有问题改写一次，改写结果问题更少且不比原文长很多才采用。"""
    if not rules.enabled:
        return Reviewed(text, (), False)
    hits = check(text, rules)
    if not hits:
        return Reviewed(text, (), False)
    better = await rewrite(text, hits)
    if (
        better
        and better.strip() != text.strip()
        and len(better) <= len(text) * 1.2 + 10
        and len(check(better, rules)) < len(hits)
    ):
        return Reviewed(better, tuple(hits), True)
    return Reviewed(text, tuple(hits), False)


def rewrite_spent(rt: Any) -> float:
    start, end = month_window(rt.budget.now())
    return rt.repo.spent_in_step(REWRITE_STEP, start, end)


def member_rewriter(rt: Any, *, room: str, name: str, model_id: str) -> Rewriter:
    """默认的改写：用这位成员自己的聊天档模型改一次，记在 room 的隐藏会话上（计入聊天预算）。"""
    from roundtable.core.chat import call_member, clean_reply  # chat 依赖本模块

    rules = rt.config.roundtable.plain_talk.check

    async def rewrite(text: str, hits: list[Hit]) -> str | None:
        if rewrite_spent(rt) + rules.estimate_usd > rules.monthly_usd + EPS:
            return None
        prompt = rt.prompts.render(
            REWRITE_ROLE,
            rt.config.roundtable.prompts[REWRITE_ROLE],
            name=name,
            problems=_neutralize(describe(hits)),
            draft=_neutralize(text),
        )
        params = dict(rt.config.roundtable.step_params.get("chat", {}))
        params["max_tokens"] = rules.max_tokens
        completion = await call_member(
            rt,
            room=room,
            model_id=model_id,
            prompt=prompt,
            step=REWRITE_STEP,
            params=params,
            estimate_usd=rules.estimate_usd,
        )
        if completion is None or completion.finish_reason == "length":
            return None
        return clean_reply(completion.text, name, rt.config.roundtable.chat.max_chars) or None

    return rewrite


async def before_speaking(rt: Any, text: str, *, room: str, name: str, model_id: str) -> str:
    """成员发言前的检查与改写（关闭时原样返回）。"""
    rules = rt.config.roundtable.plain_talk
    if not rules.enabled:
        return text
    rewrite = member_rewriter(rt, room=room, name=name, model_id=model_id)
    return (await review(text, rules.check, rewrite)).text


def _neutralize(text: str) -> str:
    for tag in TAGS:
        text = text.replace(f"</{tag}", f"<​/{tag}")
    return text
