"""按配置顺序使用搜索服务：没有 key 的跳过；限流、额度、网络等错误换下一家。

错误分类与模型渠道相同（ProviderError.failover）。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass

from roundtable.core.config import ModelsConfig
from roundtable.core.providers.errors import Attempt, ProviderError
from roundtable.core.providers.secrets import KeyRing

from .base import FetchResponse, SearchProvider, SearchResponse, search_adapter

REASON_NO_KEY = "没有 key"


class SearchUnavailable(RuntimeError):
    """没有可用的搜索服务，或全部失败。"""

    def __init__(self, message: str, attempts: tuple[Attempt, ...] = ()) -> None:
        super().__init__(message)
        self.attempts = attempts


@dataclass(frozen=True)
class SearchCall:
    provider: str
    cost_usd: float
    cost_source: str  # reported / estimated
    latency_s: float
    attempts: tuple[Attempt, ...]


class SearchService:
    def __init__(
        self,
        providers: Mapping[str, SearchProvider],
        unavailable: Mapping[str, str] | None = None,
    ) -> None:
        self.providers = dict(providers)  # 按优先顺序
        self.unavailable = dict(unavailable or {})

    @classmethod
    def build(cls, models: ModelsConfig, keys: KeyRing, timeout_s: float) -> SearchService:
        providers: dict[str, SearchProvider] = {}
        unavailable: dict[str, str] = {}
        for name, spec in models.search_providers.items():
            if not spec.enabled:
                unavailable[name] = "未启用"
                continue
            key = keys.get(spec.key_env)
            if spec.key_env and key is None:
                unavailable[name] = REASON_NO_KEY
                continue
            providers[name] = search_adapter(spec.adapter)(name, spec, key, timeout_s)
        return cls(providers, unavailable)

    @property
    def available(self) -> bool:
        return bool(self.providers)

    def reason(self) -> str:
        if self.available:
            return ""
        if not self.unavailable:
            return "没有配置搜索服务（models.yaml 的 search_providers）"
        return "；".join(f"{n}：{r}" for n, r in self.unavailable.items())

    async def _run(self, op: str, *args) -> tuple[SearchResponse | FetchResponse, SearchCall]:
        attempts: list[Attempt] = []
        for name, provider in self.providers.items():
            start = time.monotonic()
            try:
                result = await getattr(provider, op)(*args)
            except ProviderError as exc:
                attempts.append(Attempt(name, False, time.monotonic() - start, exc.kind))
                if exc.failover:
                    continue
                raise SearchUnavailable(f"搜索失败（{exc.kind}）", tuple(attempts)) from None
            latency = time.monotonic() - start
            attempts.append(Attempt(name, True, latency))
            price = provider.spec.price
            if result.cost_usd is not None:
                cost, source = result.cost_usd, "reported"
            elif op == "search":
                cost, source = price.per_search, "estimated"
            else:
                cost, source = price.per_fetch * len(args[0]), "estimated"
            return result, SearchCall(name, cost, source, latency, tuple(attempts))
        raise SearchUnavailable(
            "所有搜索服务都不可用" if attempts else self.reason(), tuple(attempts)
        )

    async def search(self, query: str, max_results: int):
        return await self._run("search", query, max_results)

    async def fetch(self, urls: list[str], max_chars: int):
        return await self._run("fetch", urls, max_chars)

    async def aclose(self) -> None:
        for provider in self.providers.values():
            await provider.aclose()
