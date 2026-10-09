"""按配置顺序使用搜索服务：没有 key 的跳过；限流、额度、网络等错误换下一家。

错误分类与模型渠道相同（ProviderError.failover）。
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
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
    relayed: bool  # 读取网页时：正文经模型转述
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
    def build(
        cls,
        models: ModelsConfig,
        keys: KeyRing,
        timeout_s: float,
        render: Callable[[str], list[dict[str, str]]] | None = None,
        render_fetch: Callable[[str], list[dict[str, str]]] | None = None,
    ) -> SearchService:
        """render / render_fetch：搜索词 / 网址 → 发给借助模型搜索、读取的服务的消息。"""
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
            provider = search_adapter(spec.adapter)(name, spec, key, timeout_s)
            if render is not None and hasattr(provider, "render"):
                provider.render = render
            if render_fetch is not None and hasattr(provider, "render_fetch"):
                provider.render_fetch = render_fetch
            providers[name] = provider
        return cls(providers, unavailable)

    @property
    def available(self) -> bool:
        return bool(self.providers)

    @property
    def can_fetch(self) -> bool:
        return any(p.supports_fetch for p in self.providers.values())

    def reason(self) -> str:
        if self.available:
            return ""
        if not self.unavailable:
            return "没有配置搜索服务（models.yaml 的 search_providers）"
        return "；".join(f"{n}：{r}" for n, r in self.unavailable.items())

    async def _run(self, op: str, *args) -> tuple[SearchResponse | FetchResponse, SearchCall]:
        attempts: list[Attempt] = []
        for name, provider in self.providers.items():
            if op == "fetch" and not provider.supports_fetch:
                continue
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
            relayed = op == "fetch" and provider.relayed_fetch
            return result, SearchCall(name, relayed, cost, source, latency, tuple(attempts))
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
