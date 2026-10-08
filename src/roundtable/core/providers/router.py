"""渠道路由：按模式筛选渠道、跳过没有 key 的渠道、出错时切换到下一个渠道。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.config import ChannelMode, ModelsConfig, ModelSpec, Route
from roundtable.core.config.schema import ChannelKind, RequestPolicy

from .base import Completion, Message, Provider
from .cost import estimate_cost
from .errors import (
    PERMANENT_KINDS,
    AllChannelsFailed,
    Attempt,
    ErrorKind,
    NoChannelAvailable,
    ProviderError,
)

log = logging.getLogger(__name__)

# 每种模式允许的渠道类型
MODE_KINDS: dict[str, frozenset[ChannelKind]] = {
    "openrouter": frozenset({"aggregator"}),
    "direct": frozenset({"direct", "local"}),
    "auto": frozenset({"aggregator", "direct", "local"}),
}
REASON_MODE = "渠道模式不包含"


@dataclass(frozen=True)
class RoutePlan:
    model: ModelSpec
    usable: tuple[Route, ...]
    skipped: dict[str, str] = field(default_factory=dict)


class ChannelRouter:
    def __init__(
        self,
        models: ModelsConfig,
        providers: Mapping[str, Provider],
        *,
        mode: ChannelMode = "auto",
        policy: RequestPolicy | None = None,
        unavailable: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._models = models
        self._providers = dict(providers)
        self._mode = mode
        self._policy = policy or RequestPolicy()
        self._unavailable = dict(unavailable or {})
        self._clock = clock
        self._sleep = sleep
        self._cooldown_until: dict[str, float] = {}

    @property
    def mode(self) -> ChannelMode:
        return self._mode

    # --- 可用性 ----------------------------------------------------------------

    def plan(self, model_id: str) -> RoutePlan:
        model = self._models.get(model_id)
        allowed = MODE_KINDS[self._mode]
        usable, skipped = [], {}
        for route in model.routes:
            kind = self._models.channels[route.channel].kind
            if kind not in allowed:
                skipped[route.channel] = f"{REASON_MODE} {kind}"
            elif route.channel not in self._providers:
                skipped[route.channel] = self._unavailable.get(route.channel, "渠道未启用")
            else:
                usable.append(route)
        return RoutePlan(model, tuple(usable), skipped)

    def available_models(self) -> list[ModelSpec]:
        """已启用、且当前模式下至少有一个可用渠道的模型。"""
        return [m for m in self._models.enabled if self.plan(m.id).usable]

    def unavailable_models(self) -> dict[str, dict[str, str]]:
        """已启用但无法调用的模型 → 各渠道的跳过原因。"""
        result = {}
        for m in self._models.enabled:
            plan = self.plan(m.id)
            if not plan.usable:
                result[m.id] = plan.skipped
        return result

    # --- 调用 ------------------------------------------------------------------

    def _ordered(self, routes: Sequence[Route]) -> list[Route]:
        """冷却中的渠道排到最后，其余保持配置顺序。"""
        now = self._clock()
        return sorted(routes, key=lambda r: self._cooldown_until.get(r.channel, 0) > now)

    def _cool_down(self, error: ProviderError) -> None:
        p = self._policy
        if error.kind in (ErrorKind.QUOTA, ErrorKind.AUTH):
            seconds = p.cooldown_quota_s
        elif error.kind in (ErrorKind.RATE_LIMIT, ErrorKind.SERVER, ErrorKind.NETWORK):
            seconds = max(p.cooldown_rate_limit_s, error.retry_after or 0)
        else:
            return
        self._cooldown_until[error.channel] = self._clock() + seconds

    async def complete(
        self,
        model_id: str,
        messages: Sequence[Message],
        params: dict[str, Any] | None = None,
    ) -> Completion:
        plan = self.plan(model_id)
        if not plan.usable:
            raise NoChannelAvailable(model_id, plan.skipped)

        attempts: list[Attempt] = []
        given_up: set[str] = set()
        last_error: ProviderError | None = None

        for round_no in range(self._policy.failover_rounds):
            remaining = [r for r in plan.usable if r.channel not in given_up]
            if not remaining:
                break
            if round_no:
                await self._sleep(self._policy.backoff_s * round_no)
            for route in self._ordered(remaining):
                started = self._clock()
                try:
                    raw = await self._providers[route.channel].complete(
                        route.model, messages, {**plan.model.params_for(route), **(params or {})}
                    )
                except ProviderError as error:
                    attempts.append(
                        Attempt(route.channel, False, self._clock() - started, error.kind)
                    )
                    last_error = error
                    self._cool_down(error)
                    if not error.failover:
                        raise AllChannelsFailed(model_id, attempts, error) from None
                    if error.kind in PERMANENT_KINDS:
                        given_up.add(route.channel)
                    log.warning("模型 %s 渠道失败，尝试下一个：%s", model_id, error)
                    continue

                latency = self._clock() - started
                attempts.append(Attempt(route.channel, True, latency))
                self._cooldown_until.pop(route.channel, None)
                if attempts[0].channel != route.channel:
                    log.info("模型 %s 已切换到渠道 %s", model_id, route.channel)
                return self._completion(plan.model, route, raw, latency, attempts)

        assert last_error is not None
        raise AllChannelsFailed(model_id, attempts, last_error)

    def _completion(self, model, route, raw, latency, attempts) -> Completion:
        if raw.reported_cost_usd is not None:
            cost, source = raw.reported_cost_usd, "reported"
        else:
            cost = estimate_cost(
                model.price_for(route), raw.input_tokens, raw.output_tokens, raw.cached_tokens
            )
            source = "estimated"
        return Completion(
            text=raw.text,
            model_id=model.id,
            channel=route.channel,
            channel_kind=self._models.channels[route.channel].kind,
            route_model=route.model,
            input_tokens=raw.input_tokens,
            output_tokens=raw.output_tokens,
            cached_tokens=raw.cached_tokens,
            cost_usd=cost,
            cost_source=source,
            latency_s=latency,
            attempts=tuple(attempts),
        )

    async def aclose(self) -> None:
        for provider in self._providers.values():
            await provider.aclose()
