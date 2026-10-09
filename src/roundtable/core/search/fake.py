"""测试用的搜索服务：按查询返回预设结果，记录收到的请求。"""

from __future__ import annotations

import hashlib
from collections import deque

from roundtable.core.config.schema import SearchProviderSpec
from roundtable.core.providers.errors import ErrorKind, ProviderError

from .base import (
    FetchedPage,
    FetchResponse,
    SearchHit,
    SearchProvider,
    SearchResponse,
    register_search,
)


class FakeSearch(SearchProvider):
    def __init__(self, name: str = "fake-search", spec: SearchProviderSpec | None = None) -> None:
        super().__init__(
            name, spec or SearchProviderSpec(adapter="fake", base_url="https://x.test")
        )
        self.queries: list[str] = []
        self.fetched: list[list[str]] = []
        self.errors: deque[ErrorKind] = deque()
        self.pages: dict[str, str] = {}

    def hits_for(self, query: str) -> tuple[SearchHit, ...]:
        slug = hashlib.sha256(query.encode()).hexdigest()[:8]
        return tuple(
            SearchHit(
                f"{query} 资料 {i}", f"https://example.test/{slug}/{i}", f"关于{query}的摘要 {i}"
            )
            for i in range(1, 4)
        )

    async def search(self, query: str, max_results: int) -> SearchResponse:
        self.queries.append(query)
        if self.errors:
            raise ProviderError(self.errors.popleft(), self.name, "fake")
        return SearchResponse(self.hits_for(query)[:max_results])

    async def fetch(self, urls: list[str], max_chars: int) -> FetchResponse:
        self.fetched.append(list(urls))
        if self.errors:
            raise ProviderError(self.errors.popleft(), self.name, "fake")
        return FetchResponse(
            tuple(FetchedPage(u, self.pages.get(u, f"{u} 的正文内容。")[:max_chars]) for u in urls)
        )


@register_search("fake")
def _factory(name, spec, key, timeout_s):
    return FakeSearch(name, spec)
