"""接力：上一场的成果交给下一场继续做（继续讨论 / 转协同 / 头脑风暴 / 交给某人单独做）。

省 token：只带上一场的最终成果（汇总、合并稿、点子榜和导图的文字版），不带过程；
去掉导图代码块、压缩空行，超过 CARRY_CHARS 字截断。不调用模型，接力本身不产生花费。
成果放在题目里的 <previous_result> 标签内，和其他外部内容一样只当资料。
"""

from __future__ import annotations

import re

CARRY_CHARS = 1500  # 带入下一场的成果最多这么多字
QUESTION_CHARS = 300  # 上一场的题目最多带这么多字

_FENCE = re.compile(r"```mermaid.*?```", re.S)
_BLANKS = re.compile(r"\n{3,}")
_TAG = re.compile(r"</?\s*previous_(?:question|result)\s*>", re.I)


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…（以下略）"


def compact(result: str, limit: int = CARRY_CHARS) -> str:
    """成果摘要：去掉导图代码块、压缩空行、截断；同名标签去掉，防止提前闭合。"""
    text = _TAG.sub("", _FENCE.sub("", result))
    return _cut(_BLANKS.sub("\n\n", text), limit)


def carry_question(previous_question: str, result: str, instruction: str) -> str:
    """下一场的题目：这一场的要求在前，上一场的题目和成果摘要放在标签里附在后面。"""
    prev = _cut(_TAG.sub("", previous_question), QUESTION_CHARS)
    return (
        f"{instruction.strip()}\n\n"
        "（以下是上一场的题目和成果摘要，供这一场接着做）\n"
        f"<previous_question>\n{prev}\n</previous_question>\n"
        f"<previous_result>\n{compact(result)}\n</previous_result>"
    )
