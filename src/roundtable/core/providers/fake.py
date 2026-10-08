"""测试与离线开发用的 Fake provider：按脚本返回结果或抛出指定错误，不联网。"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from roundtable.core.config.schema import ChannelSpec

from .base import Message, Provider, RawCompletion
from .errors import ErrorKind, ProviderError
from .registry import register_adapter
from .secrets import Secret

# 一个脚本项：文本（成功）、ErrorKind（失败）、RawCompletion，或根据请求动态生成的函数
Outcome = str | ErrorKind | RawCompletion | Callable[[str, Sequence[Message]], "Outcome"]


@dataclass(frozen=True)
class FakeCall:
    model: str
    messages: tuple[Message, ...]
    params: dict[str, Any]


class FakeProvider(Provider):
    """
    script: 模型 → 依次返回的结果；用完后返回 default。
    default: 未写脚本时的结果；默认回显最后一条消息。
    """

    def __init__(
        self,
        channel: str = "fake",
        script: dict[str, Iterable[Outcome]] | None = None,
        default: Outcome | None = None,
        reported_cost_usd: float | None = None,
    ) -> None:
        super().__init__(channel)
        self._script: dict[str, deque[Outcome]] = defaultdict(deque)
        for model, outcomes in (script or {}).items():
            self._script[model].extend(outcomes)
        self._default = default
        self._reported_cost = reported_cost_usd
        self.calls: list[FakeCall] = []

    def queue(self, model: str, *outcomes: Outcome) -> None:
        self._script[model].extend(outcomes)

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        self.calls.append(FakeCall(model, tuple(messages), dict(params)))
        if self._script[model]:
            outcome = self._script[model].popleft()
        elif self._default is not None:
            outcome = self._default
        else:
            outcome = f"[{self.channel}:{model}] {messages[-1].content if messages else ''}"
        return self._resolve(outcome, model, messages)

    def _resolve(self, outcome: Outcome, model: str, messages: Sequence[Message]) -> RawCompletion:
        if callable(outcome) and not isinstance(outcome, ErrorKind | RawCompletion):
            outcome = outcome(model, messages)
        if isinstance(outcome, ErrorKind):
            raise ProviderError(outcome, self.channel, "fake")
        if isinstance(outcome, RawCompletion):
            return outcome
        text = str(outcome)
        prompt_chars = sum(len(m.content) for m in messages)
        return RawCompletion(
            text=text,
            input_tokens=max(1, prompt_chars // 4),
            output_tokens=max(1, len(text) // 4),
            reported_cost_usd=self._reported_cost,
        )


@register_adapter("fake")
def _fake_factory(
    channel: str, spec: ChannelSpec, key: Secret | None, timeout_s: float
) -> FakeProvider:
    """允许在配置中声明 adapter: fake，用于离线演示。"""
    return FakeProvider(channel)
