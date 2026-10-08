"""从模型输出中取出 JSON 对象（容忍代码块围栏和前后多余文字）。"""

from __future__ import annotations

import json
from typing import Any


class JSONOutputError(ValueError):
    """输出中找不到合法的 JSON 对象。"""


def extract_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise JSONOutputError("输出中没有 JSON 对象") from None
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise JSONOutputError(f"JSON 格式错误：{exc.msg}") from None
    if not isinstance(value, dict):
        raise JSONOutputError("输出的 JSON 不是对象")
    return value
