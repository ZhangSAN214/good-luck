"""领域标签：学术 / 日常 / 工作 / 娱乐 / 社交 / 身心（``chat.memory.domains``）。

夜间整理时由模型给每件事标一个；没标或标了表外的归「日常」。所有成员用同一张表（中立）。
"""

from __future__ import annotations

from collections.abc import Sequence

DEFAULT = "日常"


def normalize(domain: str | None, domains: Sequence[str]) -> str | None:
    """模型给的标签：在表里就用，不在返回 None。"""
    d = (domain or "").strip()
    return d if d in domains else None


def tag(text: str, given: str | None, domains: Sequence[str]) -> str:
    return normalize(given, domains) or (DEFAULT if DEFAULT in domains else domains[0])
