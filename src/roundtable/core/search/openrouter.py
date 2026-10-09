"""OpenRouter 自带的联网搜索（与模型调用共用 OPENROUTER_API_KEY）。

做法：用一个便宜的模型发一次 /chat/completions，打开联网搜索，读取响应中的 url_citation 注释
（标题、链接、摘要）作为搜索结果；模型自己写的文字不用。
- request: plugin（默认）= `plugins: [{"id": "web", "engine": ..., "max_results": N}]`；
  server_tool = `tools: [{"type": "openrouter:web_search", "parameters": {...}}]`（新接口）。
- 计费：搜索引擎按次收费（如 exa 每次 $0.007，含最多 10 条结果）+ 该模型的 token 费；
  响应里的 usage.cost 是两者合计，以它为准。
- 不支持读取网页正文（fetch）：有 Tavily 时由它负责，否则 fetch 工具关闭。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from roundtable.core.config.schema import SearchProviderSpec
from roundtable.core.providers._http import error_fields, kind_for_status, retry_after, safe_detail
from roundtable.core.providers.errors import ErrorKind, ProviderError
from roundtable.core.providers.secrets import Secret

from .base import FetchResponse, SearchHit, SearchProvider, SearchResponse, register_search


class OpenRouterSearch(SearchProvider):
    supports_fetch = False

    def __init__(
        self,
        name: str,
        spec: SearchProviderSpec,
        key: Secret | None,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(name, spec)
        params = dict(spec.params)
        self.model = str(params.pop("model", ""))
        if not self.model:
            raise ValueError(f"搜索服务 {name} 需要在 params.model 中指定用于搜索的模型")
        self.request = str(params.pop("request", "plugin"))
        # 发给搜索用模型的消息（prompts/web_search，由 SearchService 设置）；参数为搜索词
        self.render: Callable[[str], list[dict[str, str]]] = lambda q: [
            {"role": "user", "content": q}
        ]
        self.options = params  # engine、max_results 以外的选项原样传给搜索
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Authorization"] = f"Bearer {key.reveal()}"
        self._secrets = [key.reveal()] if key is not None else []
        self._client = httpx.AsyncClient(
            base_url=spec.base_url, headers=headers, timeout=timeout_s, transport=transport
        )

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.name, safe_detail(detail, self._secrets), **kw)

    def _body(self, query: str, max_results: int) -> dict[str, Any]:
        search = {**self.options, "max_results": max_results}
        body: dict[str, Any] = {
            "model": self.model,
            "messages": self.render(query),
            "max_tokens": 300,
            "usage": {"include": True},
        }
        if self.request == "server_tool":
            body["tools"] = [{"type": "openrouter:web_search", "parameters": search}]
        else:
            body["plugins"] = [{"id": "web", **search}]
        return body

    async def search(self, query: str, max_results: int) -> SearchResponse:
        try:
            response = await self._client.post(
                "chat/completions", json=self._body(query, max_results)
            )
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
            raise self._error(
                kind_for_status(response.status_code, code),
                message or (payload if isinstance(payload, str) else ""),
                status=response.status_code,
                retry_after=retry_after(response.headers),
            ) from None
        try:
            data = response.json()
            message = data["choices"][0]["message"]
            annotations = message.get("annotations") or []
            usage = data.get("usage") or {}
            cost = usage.get("cost")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析返回内容") from None
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in annotations:
            cite = (item or {}).get("url_citation") if isinstance(item, dict) else None
            if not isinstance(cite, dict):
                continue
            url = str(cite.get("url") or "")
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            hits.append(
                SearchHit(str(cite.get("title") or url), url, str(cite.get("content") or ""))
            )
        return SearchResponse(tuple(hits[:max_results]), float(cost) if cost is not None else None)

    async def fetch(self, urls: list[str], max_chars: int) -> FetchResponse:
        raise ProviderError(ErrorKind.BAD_REQUEST, self.name, "不支持读取网页")

    async def aclose(self) -> None:
        await self._client.aclose()


@register_search("openrouter")
def _factory(name: str, spec: SearchProviderSpec, key: Secret | None, timeout_s: float):
    return OpenRouterSearch(name, spec, key, timeout_s)
