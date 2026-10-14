"""从模型输出中取出 JSON 对象。

容忍：代码块围栏、前后多余文字、字符串里的原始换行、非法的反斜杠转义（LaTeX 公式里的 \\frac 之类）、
对象 / 数组末尾多余的逗号、只输出了一个数组（包成 {"_list": [...]}，由调用方的 schema 决定怎么用）。
"""

from __future__ import annotations

import json
import re
from typing import Any


class JSONOutputError(ValueError):
    """输出中找不到合法的 JSON 对象。"""


# 反斜杠后面不是合法的转义字符时，把反斜杠本身转义（\frac → \\frac）
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})')
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _loads(text: str) -> Any:
    # strict=False：允许字符串里出现原始换行与制表符。模型把多行 Markdown 放进 JSON 字符串时
    # 常常不转义换行，严格模式会整份拒绝（长篇的汇总 / 合并因此失败）。
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        fixed = _TRAILING_COMMA.sub(r"\1", _BAD_ESCAPE.sub(r"\\\\", text))
        if fixed == text:
            raise
        return json.loads(fixed, strict=False)


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = _loads(text)
    except json.JSONDecodeError:
        value = None
        for open_, close in (("{", "}"), ("[", "]")):
            start, end = text.find(open_), text.rfind(close)
            if start == -1 or end <= start:
                continue
            try:
                value = _loads(text[start : end + 1])
                break
            except json.JSONDecodeError as exc:
                last = exc.msg
        if value is None:
            if "last" in locals():
                raise JSONOutputError(f"JSON 格式错误：{last}") from None
            raise JSONOutputError("输出中没有 JSON 对象") from None
    if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
        return {"_list": value}
    if not isinstance(value, dict):
        raise JSONOutputError("输出的 JSON 不是对象")
    return value
