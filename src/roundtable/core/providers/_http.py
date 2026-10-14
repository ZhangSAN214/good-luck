"""适配器共用的 HTTP 错误归类与脱敏工具。"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .errors import ErrorKind
from .secrets import redact

_SNIPPET = 200
# 出现在 429 错误 code/type 中、表示额度用尽而非限流的值
QUOTA_CODES = frozenset({"insufficient_quota", "billing_error", "insufficient_balance"})


def kind_for_status(status: int, code: str | None = None) -> ErrorKind:
    if status == 429:
        return ErrorKind.QUOTA if code in QUOTA_CODES else ErrorKind.RATE_LIMIT
    if status == 402:
        return ErrorKind.QUOTA
    if status in (401, 403):
        return ErrorKind.AUTH
    if status == 404:
        return ErrorKind.NOT_FOUND
    if status == 408:
        return ErrorKind.TIMEOUT
    if status >= 500:
        return ErrorKind.SERVER
    return ErrorKind.BAD_REQUEST


def error_fields(body: Any) -> tuple[str | None, str]:
    """从常见错误体中取出 (code, message)。兼容 OpenAI / OpenRouter / Gemini 风格。"""
    if not isinstance(body, Mapping):
        return None, ""
    err = body.get("error", body)
    if not isinstance(err, Mapping):
        return None, str(err)
    code = err.get("code") or err.get("type") or err.get("status")
    return (str(code) if code is not None else None), str(err.get("message", ""))


def safe_detail(text: str, secrets: Iterable[str] = ()) -> str:
    """脱敏并截断外部文本。"""
    text = redact(" ".join(text.split()), secrets)
    return text[:_SNIPPET] + ("…" if len(text) > _SNIPPET else "")


def retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
