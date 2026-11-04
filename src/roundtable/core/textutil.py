"""通用小工具：文字截断、防提示注入的标签处理、宽松转数字、时间格式。

代码体检（2026-11-04）发现这些几行的小函数在十几个模块里各写了一份，这里收成一份。
纯函数，不依赖项目里的其他模块，任何一层都能用。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, date, datetime
from typing import Any

ZWSP = "​"  # 零宽空格：插进标签里，让外部内容里的标签失效


def neutralize(text: str, tags: Iterable[str], *, opening: bool = False) -> str:
    """防止内容中出现的结束标签提前闭合外层标签（提示注入）。

    ``opening=True`` 时开始标签也一起打断（工具结果里不许伪造新的工具调用）。"""
    for tag in tags:
        text = text.replace(f"</{tag}", f"<{ZWSP}/{tag}")
        if opening:
            text = text.replace(f"<{tag}", f"<{ZWSP}{tag}")
    return text


def flatten(text: Any) -> str:
    """把连续空白（含换行）压成一个空格，去掉首尾空白；None 当空字符串。"""
    return " ".join(str(text or "").split())


def ellipsize(text: Any, limit: int, *, rstrip: bool = False) -> str:
    """压成一行，超过 ``limit`` 字就截断并以「…」结尾（结果正好 ``limit`` 字）。

    ``rstrip=True`` 时截断处的空白先去掉再接「…」。"""
    text = flatten(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    return (cut.rstrip() if rstrip else cut) + "…"


def flat_cut(text: Any, limit: int) -> str:
    """压成一行后硬截到 ``limit`` 字（不加省略号）。"""
    return flatten(text)[:limit]


def attr_text(text: str) -> str:
    """放进 XML 属性值里的文字：压成一行，引号换成单引号，尖括号换成全角样式。"""
    return flatten(text).replace('"', "'").replace("<", "‹").replace(">", "›")


def to_float(x: Any, default: float = 3.0) -> float:
    """宽松转成小数；转不了就用 ``default``（模型打分常用 1～5 分，默认取中间的 3）。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def half_life(age: float, half: float) -> float:
    """半衰期衰减：过了 ``half`` 剩一半；负的时间当 0。"""
    return 0.5 ** (max(age, 0.0) / half)


def load_json_dict(raw: str | None) -> dict:
    """解析 JSON 文本，只接受对象；空、坏、不是对象都返回空字典。"""
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_seconds(t: datetime) -> str:
    """换成 UTC、精确到秒的 ISO 时间。"""
    return t.astimezone(UTC).isoformat(timespec="seconds")


def iso_week(day: date) -> str:
    """ISO 周编号，如 ``2026-W45``。"""
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"
