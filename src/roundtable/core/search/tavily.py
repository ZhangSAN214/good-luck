"""Tavily 搜索（POST /search、POST /extract；key 放在 Authorization 请求头）。"""

from __future__ import annotations

from typing import Any

import httpx

from roundtable.core.config.schema import SearchProviderSpec
from roundtable.core.providers._http import error_fields, kind_for_status, retry_after, safe_detail
from roundtable.core.providers.errors import ErrorKind, ProviderError
from roundtable.core.providers.secrets import Secret

from .base import (
    FetchedPage,
    FetchResponse,
    SearchHit,
    SearchProvider,
    SearchResponse,
    register_search,
)

# Tavily 用 432 / 433 表示套餐额度或按量额度用完
_QUOTA_STATUS = {432, 433}


class TavilySearch(SearchProvider):
    def __init__(
        self,
        name: str,
        spec: SearchProviderSpec,
        key: Secret | None,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(name, spec)
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Authorization"] = f"Bearer {key.reveal()}"
        self._secrets = [key.reveal()] if key is not None else []
        self._client = httpx.AsyncClient(
            base_url=spec.base_url, headers=headers, timeout=timeout_s, transport=transport
        )

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.name, safe_detail(detail, self._secrets), **kw)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(path, json=body)
        except httpx.TimeoutException:
            raise self._error(ErrorKind.TIMEOUT) from None
        except httpx.TransportError as exc:
            raise self._error(ErrorKind.NETWORK, type(exc).__name__) from None
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            code, message = error_fields(payload)
            if not message and isinstance(payload, dict):
                message = str(payload.get("detail", ""))
            status = response.status_code
            kind = ErrorKind.QUOTA if status in _QUOTA_STATUS else kind_for_status(status, code)
            raise self._error(
                kind,
                message or (payload if isinstance(payload, str) else ""),
                status=status,
                retry_after=retry_after(response.headers),
            ) from None
        try:
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError
            return data
        except (ValueError, TypeError):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析返回内容") from None

    async def search(self, query: str, max_results: int) -> SearchResponse:
        body = {**self.spec.params, "query": query, "max_results": max_results}
        data = await self._post("search", body)
        try:
            hits = tuple(
                SearchHit(
                    title=str(r.get("title") or r.get("url")),
                    url=str(r["url"]),
                    snippet=str(r.get("content") or ""),
                )
                for r in data.get("results") or []
                if isinstance(r, dict) and str(r.get("url", "")).startswith(("http://", "https://"))
            )
        except (KeyError, TypeError, AttributeError):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析搜索结果") from None
        return SearchResponse(hits[:max_results])

    async def fetch(self, urls: list[str], max_chars: int) -> FetchResponse:
        data = await self._post("extract", {"urls": urls, "format": "text"})
        by_url = {}
        for r in data.get("results") or []:
            if isinstance(r, dict) and r.get("url"):
                by_url[str(r["url"])] = str(r.get("raw_content") or "")[:max_chars]
        pages = tuple(
            FetchedPage(u, by_url[u]) if u in by_url else FetchedPage(u, "", ok=False) for u in urls
        )
        return FetchResponse(pages)

    async def aclose(self) -> None:
        await self._client.aclose()


@register_search("tavily")
def _factory(name: str, spec: SearchProviderSpec, key: Secret | None, timeout_s: float):
    return TavilySearch(name, spec, key, timeout_s)
