"""规则判断：不调用模型，按 routing.yaml 的规则给出难度、必需标签、题型。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from roundtable.core.config.schema import Difficulty, RuleCondition, TriageRule


@dataclass(frozen=True)
class Question:
    text: str
    # 附件类型（image / pdf / docx / text / audio），供规则判断使用
    attachments: tuple[str, ...] = ()
    # 附件内容的 token 数（文字版 / 提取的文字 / 图片按固定值），用于花费预估
    attachment_tokens: int = 0


@dataclass(frozen=True)
class TriageResult:
    difficulty: Difficulty | None = None
    difficulty_rule: str | None = None
    task_type: str | None = None
    require_tags: tuple[str, ...] = ()
    matched: tuple[str, ...] = field(default_factory=tuple)


def matches(cond: RuleCondition, q: Question) -> bool:
    text = q.text.strip()
    lowered = text.lower()
    n = len(text)
    if cond.min_chars is not None and n < cond.min_chars:
        return False
    if cond.max_chars is not None and n > cond.max_chars:
        return False
    if cond.any_keywords and not any(k.lower() in lowered for k in cond.any_keywords):
        return False
    if cond.no_keywords and any(k.lower() in lowered for k in cond.no_keywords):
        return False
    return not (cond.attachment_types and not set(cond.attachment_types) & set(q.attachments))


def triage(q: Question, rules: Sequence[TriageRule]) -> TriageResult:
    difficulty = rule_name = task_type = None
    tags: list[str] = []
    matched: list[str] = []
    for rule in rules:
        if not matches(rule.when, q):
            continue
        action = rule.then
        # 难度只由第一条给出难度的规则决定；标签和题型照常累积
        if action.difficulty is not None and difficulty is None:
            difficulty, rule_name = action.difficulty, rule.name
        matched.append(rule.name)
        tags.extend(t for t in action.require_tags if t not in tags)
        if action.task_type and task_type is None:
            task_type = action.task_type
    return TriageResult(difficulty, rule_name, task_type, tuple(tags), tuple(matched))
