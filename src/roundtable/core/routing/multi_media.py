"""讨论模式下，题目是否需要生成多个媒体文件（规则判断，不调用模型）。

讨论模式里每位成员都会把整题各做一遍：要画多张图、做多段媒体时，每人都生成一整套，
实际花费会是预估的好几倍。检测到时，提交前建议改用协同模式（拆分后每人只做一部分）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from roundtable.core.config.schema import MultiMediaRule

_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_VAGUE = ("几", "多", "若干", "数")  # 几张 / 多张 / 若干张 / 数张


@dataclass(frozen=True)
class MultiMediaHint:
    count: int | None  # 识别出的数量；只靠关键词判断时为 None
    reason: str  # 给用户看的依据（题目里的原话）


def _number(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    if token == "十":
        return 10
    if token.startswith("十") and len(token) == 2 and token[1] in _DIGITS:
        return 10 + _DIGITS[token[1]]
    if len(token) == 2 and token[1] == "十" and token[0] in _DIGITS:
        return _DIGITS[token[0]] * 10
    if len(token) == 1 and token in _DIGITS:
        return _DIGITS[token]
    return None


def detect_multi_media(text: str, rule: MultiMediaRule | None) -> MultiMediaHint | None:
    """返回依据；不需要多个媒体文件（或规则关闭）时返回 None。"""
    if rule is None or not rule.enabled or not text.strip():
        return None
    nouns = sorted(rule.nouns, key=len, reverse=True)
    noun = "|".join(re.escape(n) for n in nouns)
    classifier = "|".join(re.escape(c) for c in rule.classifiers) or "(?!)"
    number = r"\d{1,2}|十[一二三四五六七八九]?|[一二两三四五六七八九]十?[一二三四五六七八九]?"
    # 数量 + 量词 + （最多 8 个不含标点的字）+ 媒体名词：「四张不同风格的图」「3 个短视频」
    pattern = re.compile(
        rf"(?P<num>{number})\s*(?:{classifier})[^，。,.；;！!？?\n]{{0,8}}?(?:{noun})", re.I
    )
    for m in pattern.finditer(text):
        n = _number(m.group("num"))
        if n is not None and n >= rule.min_count:
            return MultiMediaHint(n, m.group(0).strip())
    lowered = text.lower()
    has_noun = any(n.lower() in lowered for n in rule.nouns)
    # 「多张图」「几个视频」这类含糊的数量
    vague = re.compile(
        rf"(?:{'|'.join(_VAGUE)})\s*(?:{classifier})[^，。,.；;！!？?\n]{{0,8}}?(?:{noun})", re.I
    )
    m = vague.search(text)
    if m:
        return MultiMediaHint(None, m.group(0).strip())
    if has_noun:
        for hint in rule.hints:
            if hint.lower() in lowered:
                return MultiMediaHint(None, hint)
    return None


def collab_advice(hint: MultiMediaHint) -> dict[str, object]:
    """提交前提示的内容（可 JSON 化；网页、命令行共用）。"""
    how = f"需要生成约 {hint.count} 个媒体文件" if hint.count else "看起来需要生成多个媒体文件"
    return {
        "kind": "multi_media",
        "applies_to": "discussion",
        "suggest_workflow": "collab",
        "count": hint.count,
        "reason": hint.reason,
        "message": (
            f"题目里提到「{hint.reason}」，{how}。讨论模式会让每位成员各自把整题做一遍"
            "（每人各生成一整套），实际花费可能是预估的好几倍；建议切换到协同模式，"
            "拆分后每人只做一部分。"
        ),
    }
