"""工作台每月花了多少：dsh 直接用模型服务的 key，钱不经过圆桌，只能用余额差值估算。

- 只在 dsh 运行期间记：启动时以当前余额为起点（停着的时候余额变少不算工作台的）。
- 余额变多是充值，不算花费，只把起点挪上去。
- 圆桌自己也用同一个 key 的渠道（``dsh.shared_channels``）在同一段时间里的花费，从差值里扣掉。
- UTC 自然月：每月 1 号自动从 0 开始。
- 账本存成 ``data/dsh/usage.json``（本机，不进 git、不进数据库）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


def month_key(at: datetime) -> str:
    return at.strftime("%Y-%m")


@dataclass
class UsageState:
    month: str = ""
    spent_usd: float = 0.0
    last_balance_usd: float | None = None
    last_check: str | None = None  # ISO 时间
    currency: str | None = None
    last_raw: float | None = None
    checks: int = 0
    history: dict[str, float] = field(default_factory=dict)  # 往月：{"2026-09": 1.23}


class UsageLedger:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.state = self._load()

    def _load(self) -> UsageState:
        if self.path is None or not self.path.is_file():
            return UsageState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return UsageState(**data)
        except (OSError, ValueError, TypeError):
            return UsageState()  # 账本坏了就从头记，不影响工作台使用

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(asdict(self.state), ensure_ascii=False), encoding="utf-8")

    def _roll(self, now: datetime) -> None:
        key = month_key(now)
        if self.state.month != key:
            if self.state.month:
                self.state.history[self.state.month] = round(self.state.spent_usd, 6)
            self.state.month = key
            self.state.spent_usd = 0.0

    def spent(self, now: datetime) -> float:
        return self.state.spent_usd if self.state.month == month_key(now) else 0.0

    def rebase(self, balance_usd: float, now: datetime, currency: str, raw: float) -> None:
        """dsh 启动时：以当前余额为起点，之前的变化不算。"""
        self._roll(now)
        self._mark(balance_usd, now, currency, raw)
        self._save()

    def observe(
        self, balance_usd: float, now: datetime, currency: str, raw: float, shared_usd: float = 0.0
    ) -> float:
        """dsh 运行中又查了一次余额：返回这次算进工作台的花费。"""
        self._roll(now)
        added = 0.0
        last = self.state.last_balance_usd
        if last is not None:
            drop = last - balance_usd
            if drop > 0:
                added = max(0.0, drop - max(0.0, shared_usd))
                self.state.spent_usd += added
        self._mark(balance_usd, now, currency, raw)
        self._save()
        return added

    def last_check(self) -> datetime | None:
        if not self.state.last_check:
            return None
        return datetime.fromisoformat(self.state.last_check)

    def _mark(self, balance_usd: float, now: datetime, currency: str, raw: float) -> None:
        self.state.last_balance_usd = balance_usd
        self.state.last_check = now.isoformat()
        self.state.currency = currency
        self.state.last_raw = raw
        self.state.checks += 1
