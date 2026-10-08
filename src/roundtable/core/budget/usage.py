"""用量统计：按渠道、按模型累计 token 与费用（内存中；持久化在存储阶段实现）。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from roundtable.core.providers import Attempt, Completion


@dataclass
class UsageTotals:
    calls: int = 0
    failed_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0


class UsageLedger:
    def __init__(self) -> None:
        self._by_channel: dict[str, UsageTotals] = defaultdict(UsageTotals)
        self._by_model: dict[str, UsageTotals] = defaultdict(UsageTotals)

    def record(self, completion: Completion) -> None:
        """记录一次成功调用；其中失败的渠道尝试计入对应渠道的 failed_attempts。"""
        self.record_failures(a for a in completion.attempts if not a.ok)
        for totals in (self._by_channel[completion.channel], self._by_model[completion.model_id]):
            totals.calls += 1
            totals.input_tokens += completion.input_tokens
            totals.output_tokens += completion.output_tokens
            totals.cached_tokens += completion.cached_tokens
            totals.cost_usd += completion.cost_usd

    def record_failures(self, attempts: Iterable[Attempt]) -> None:
        for attempt in attempts:
            if not attempt.ok:
                self._by_channel[attempt.channel].failed_attempts += 1

    def by_channel(self) -> dict[str, UsageTotals]:
        return dict(self._by_channel)

    def by_model(self) -> dict[str, UsageTotals]:
        return dict(self._by_model)

    def total(self) -> UsageTotals:
        out = UsageTotals()
        for t in self._by_channel.values():
            out.calls += t.calls
            out.failed_attempts += t.failed_attempts
            out.input_tokens += t.input_tokens
            out.output_tokens += t.output_tokens
            out.cached_tokens += t.cached_tokens
            out.cost_usd += t.cost_usd
        return out
