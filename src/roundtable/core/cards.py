"""确认卡片：所有需要用户拍板的地方都用同一种格式 —— 现状 / 选项 / 各选项代价 / 推荐。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def money(usd: float) -> str:
    """金额显示：不足 1 美元时保留 4 位小数，避免小额显示成 $0.00。"""
    return f"${usd:.2f}" if abs(usd) >= 1 else f"${usd:.4f}"


DIFFICULTY_LABELS = {"simple": "简单", "medium": "中等", "hard": "困难"}


@dataclass(frozen=True)
class CardOption:
    key: str  # 回复时使用的值，如 continue / stop
    label: str
    cost_usd: float | None = None  # 选择该项预计还要花的钱
    note: str = ""


@dataclass(frozen=True)
class ConfirmationCard:
    kind: str  # cost / escalation / budget …
    situation: str  # 现状
    options: tuple[CardOption, ...]  # 选项与代价
    recommendation: str  # 推荐的选项 key
    reason: str = ""  # 推荐理由
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        keys = [o.key for o in self.options]
        if len(keys) != len(set(keys)):
            raise ValueError("选项 key 不能重复")
        if self.recommendation not in keys:
            raise ValueError("推荐项必须是选项之一")

    def option_keys(self) -> list[str]:
        return [o.key for o in self.options]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
