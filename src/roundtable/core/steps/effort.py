"""防偷懒：检查成员产出是否有实质内容；不合格打回重做一次，仍不合格标记"敷衍"。

规则全部在 roundtable.yaml 的 effort_check，只看文本本身（不看是哪个模型）。
重做在原对话后追加一轮：上一次的输出作为 assistant，再附上 redo 提示词说明原因。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from difflib import SequenceMatcher

from roundtable.core.config.schema import EffortCheck
from roundtable.core.prompts import RenderedPrompt
from roundtable.core.providers import Message

from .base import CallOutcome, TableContext, call_model
from .schemas import EffortRecord

_PUNCT = re.compile(r"[\s\W_]+", re.UNICODE)


def _norm(text: str) -> str:
    return _PUNCT.sub("", text.lower())


def similarity(a: str, b: str) -> float:
    """去掉空白和标点后的相似度（0–1）。"""
    x, y = _norm(a), _norm(b)
    if not x or not y:
        return 0.0
    return SequenceMatcher(None, x, y, autojunk=False).ratio()


def min_chars(rule: EffortCheck, expected_tokens: int | None) -> int:
    return max(rule.min_chars, round((expected_tokens or 0) * rule.chars_per_expected_token))


def _only_phrases(text: str, phrases: list[str]) -> bool:
    rest = _norm(text)
    for phrase in sorted({_norm(p) for p in phrases}, key=len, reverse=True):
        if phrase:
            rest = rest.replace(phrase, "")
    return not rest


def text_problems(
    text: str,
    *,
    rule: EffortCheck,
    question: str,
    expected_tokens: int | None,
    peers: Mapping[str, str] | None = None,
    label=lambda code: code,
) -> list[str]:
    """一段正文（答案 / 修订稿）的问题；空列表表示合格。

    peers：其他组员的答案（代号 → 正文），用于发现照抄；作答步骤各自独立，不需要传。
    """
    body = text.strip()
    problems: list[str] = []
    if not body or _only_phrases(body, rule.empty_phrases):
        return ["只有空话，没有实质内容"]
    lowered = body.lower()
    if len(body) <= rule.refusal_max_chars and any(
        p.lower() in lowered for p in rule.refusal_phrases
    ):
        hit = next(p for p in rule.refusal_phrases if p.lower() in lowered)
        problems.append(f"拒绝作答或只表示无法完成（{len(body)} 字且含「{hit}」）")
    need = min_chars(rule, expected_tokens)
    if len(body) < need:
        basis = (
            f"预估答案约 {expected_tokens} token × {rule.chars_per_expected_token}"
            if expected_tokens and need > rule.min_chars
            else f"最低 {rule.min_chars} 字"
        )
        problems.append(f"内容过短（{len(body)} 字，门槛 {need} 字：{basis}）")
    sim = similarity(body, question)
    if sim >= rule.restate_similarity:
        problems.append(
            f"基本只是复述题目，没有作答（与题目相似度 {sim:.2f}，门槛 {rule.restate_similarity}）"
        )
    for code, other in (peers or {}).items():
        if min(len(body), len(other.strip())) >= rule.duplicate_min_chars:
            sim = similarity(body, other)
            if sim >= rule.duplicate_similarity:
                problems.append(
                    f"与{label(code)}的答案几乎相同，疑似照抄"
                    f"（相似度 {sim:.2f}，门槛 {rule.duplicate_similarity}）"
                )
    return problems


# 输出被长度上限截断时，这两类问题是截断造成的，不是成员偷懒
TRUNCATION_EXPLAINED = ("内容过短", "没有回应")


def forgive_truncation(
    ctx: TableContext,
    step: str,
    code: str,
    call_id: int | None,
    found: list[str],
    item: str | None = None,
) -> list[str]:
    """产出来自被长度上限截断的调用时，去掉"过短""没回应审阅意见"这类由截断造成的问题。

    去掉了问题时登记一条 status="truncated" 的检查记录（界面显示"输出被截断，未判为敷衍"）；
    之后如果还有别的真问题，后续的 redone / lazy 记录会覆盖它。
    """
    if call_id is None or call_id not in ctx.truncated_calls:
        return found
    kept = [p for p in found if not p.startswith(TRUNCATION_EXPLAINED)]
    forgiven = [p for p in found if p.startswith(TRUNCATION_EXPLAINED)]
    if forgiven and not kept:
        reasons = tuple(f"输出被长度上限截断（{p}）" for p in forgiven)
        record_effort(ctx, EffortRecord(step, code, "truncated", reasons, (), False, item))
    return kept


async def redo_call(
    ctx: TableContext,
    *,
    step: str,
    role: str,
    model_id: str,
    prompt: RenderedPrompt,
    previous: str,
    reasons: list[str],
    code: str | None,
) -> CallOutcome:
    """打回重做：同一对话追加"上一次的输出 + 重做说明"，提示词与参数对所有成员相同。"""
    redo = ctx.prompts.get("redo", ctx.prompt_version("redo"))
    filled = redo.render(reasons="\n".join(f"- {r}" for r in reasons))
    system = "\n\n".join(m.content for m in prompt.messages if m.role == "system")
    extra_system = next(m.content for m in filled.messages if m.role == "system")
    messages = (
        Message("system", f"{system}\n\n{extra_system}"),
        *(m for m in prompt.messages if m.role != "system"),
        Message("assistant", previous),
        *(m for m in filled.messages if m.role != "system"),
    )
    combined = RenderedPrompt(filled.role, filled.version, filled.sha256, messages)
    ctx.emit("effort_redo", step, code, reasons=list(reasons))
    return await call_model(
        ctx, step=step, role=role, model_id=model_id, prompt=combined, code=code
    )


def record_effort(ctx: TableContext, record: EffortRecord) -> None:
    """保存检查结果（作为一种产出），供界面、汇总标注和贡献统计使用。"""
    ctx.state.effort[record.key] = record
    ctx.repo.save_output(
        ctx.session_id,
        table_no=ctx.table_no,
        step=record.step,
        kind="effort",
        code=record.code,
        content=record.to_dict(),
    )
    if record.lazy:
        ctx.emit("effort_flagged", record.step, record.code, reasons=list(record.final_reasons))
