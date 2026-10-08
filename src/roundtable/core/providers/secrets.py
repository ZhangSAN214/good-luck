"""密钥处理：只从环境变量 / .env 读取；对外表示一律遮蔽；外部文本入日志或异常前先脱敏。"""

from __future__ import annotations

import logging
import os
import re
import threading
import traceback
from collections.abc import Iterable, Mapping
from pathlib import Path

from dotenv import dotenv_values

MASK = "***"

# 常见 key 格式。用于脱敏外部返回的文本（例如错误信息里回显的 key）。
_KEY_PATTERNS = [
    re.compile(r"sk-or-v1-[0-9A-Za-z]{8,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"xai-[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{8,}"),
]
# 短于此长度的值不做字面替换，避免误伤普通文本
_MIN_LITERAL = 8

# 进程内所有已加载的密钥；用于日志与外部文本脱敏
_REGISTRY: set[str] = set()
_LOCK = threading.Lock()
_factory_installed = False


class Secret:
    """包装一个密钥。repr / str / 格式化都只显示 ***；只有 reveal() 返回原值。"""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("Secret 不能为空")
        self._value = value
        _register(value)

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return f"Secret('{MASK}')"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __reduce__(self):  # 禁止被序列化进数据库或缓存
        raise TypeError("Secret 不可序列化")


class KeyRing:
    """按环境变量名保存密钥。缺失或为空的变量视为没有 key。"""

    def __init__(self, keys: Mapping[str, Secret]) -> None:
        self._keys = dict(keys)

    @classmethod
    def from_env(
        cls,
        names: Iterable[str],
        environ: Mapping[str, str] | None = None,
        dotenv_path: Path | str | None = None,
    ) -> KeyRing:
        """environ 优先于 .env 文件；两者都没有的变量视为缺失。"""
        merged: dict[str, str] = {}
        if dotenv_path is not None and Path(dotenv_path).is_file():
            merged.update({k: v for k, v in dotenv_values(dotenv_path).items() if v})
        merged.update(os.environ if environ is None else environ)
        keys = {}
        for name in names:
            value = (merged.get(name) or "").strip()
            if value:
                keys[name] = Secret(value)
        return cls(keys)

    def get(self, name: str | None) -> Secret | None:
        return self._keys.get(name) if name else None

    def has(self, name: str | None) -> bool:
        return self.get(name) is not None

    def secret_values(self) -> list[str]:
        """供脱敏使用。"""
        return [s.reveal() for s in self._keys.values()]

    def __repr__(self) -> str:
        return f"KeyRing(names={sorted(self._keys)})"


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    """从外部文本中去掉已知密钥（含所有已加载的 Secret）和疑似密钥。"""
    with _LOCK:
        known = set(_REGISTRY)
    for value in known.union(secrets):
        if value and len(value) >= _MIN_LITERAL:
            text = text.replace(value, MASK)
    for pattern in _KEY_PATTERNS:
        text = pattern.sub(MASK, text)
    return text


# --- 日志脱敏 ------------------------------------------------------------------
# 第三方库（如 SDK 的 DEBUG 日志）可能输出响应头、原始异常等；它们不受我们控制，
# 因此在日志记录创建时统一脱敏，覆盖所有 logger 和回溯文本。


def _register(value: str) -> None:
    global _factory_installed
    with _LOCK:
        _REGISTRY.add(value)
        if _factory_installed:
            return
        _factory_installed = True
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        _redact_record(record)
        return record

    logging.setLogRecordFactory(factory)


def _redact_record(record: logging.LogRecord) -> None:
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - 格式化失败时保留原样，交给 logging 自己报告
        return
    record.msg, record.args = redact(message), None
    if record.exc_info and record.exc_info[0] is not None:
        record.exc_text = redact("".join(traceback.format_exception(*record.exc_info)).rstrip())
        record.exc_info = None
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
