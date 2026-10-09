"""OpenRouter 自带的联网搜索（与模型调用共用 OPENROUTER_API_KEY）。

做法：用一个便宜的模型发一次 /chat/completions，打开联网搜索，读取响应中的 url_citation 注释
（标题、链接、摘要）作为搜索结果；模型自己写的文字不用。
- request: plugin（默认）= `plugins: [{"id": "web", "engine": ..., "max_results": N}]`；
  server_tool = `tools: [{"type": "openrouter:web_search", "parameters": {...}}]`（新接口）。
- 计费：搜索引擎按次收费（如 exa 每次 $0.007，含最多 10 条结果）+ 该模型的 token 费；
  响应里的 usage.cost 是两者合计，以它为准。
- 读取网页正文（fetch）：用同一个模型调用 `openrouter:web_fetch` 服务端工具（取回的正文只交给
  模型，不直接返回给调用方），并要求模型原样输出正文；读取失败时输出约定的标记 FETCH_FAILED。
  正文因此经过模型转述，可能不完整，结果标注 via="model"。计费：取回本身按引擎收费（openrouter
  引擎免费，exa 每次 $0.001）+ 模型输出正文的 token 费，以 usage.cost 为准。
  `params.fetch: false` 可关闭（改由 Tavily 等直接取回正文的服务负责）。
"""

from __future__ import annotations

from collections.abc import Callable
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

FAILED = "FETCH_FAILED"  # 与 prompts/web_fetch 中约定的失败标记一致


class OpenRouterSearch(SearchProvider):
    supports_fetch = True
    relayed_fetch = True  # 正文经模型转述

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
        self.supports_fetch = bool(params.pop("fetch", True))
        # 读取网页时放进 tools 的对象；默认只写类型（引擎默认 auto：原生取回，否则 exa）
        self.fetch_tool = dict(params.pop("fetch_tool", {"type": "openrouter:web_fetch"}))
        # 发给搜索用模型的消息（prompts/web_search，由 SearchService 设置）；参数为搜索词
        self.render: Callable[[str], list[dict[str, str]]] = lambda q: [
            {"role": "user", "content": q}
        ]
        self.render_fetch: Callable[[str], list[dict[str, str]]] = lambda u: [
            {"role": "user", "content": u}
        ]
        self.options = params  # engine 等选项原样传给搜索
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

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post("chat/completions", json=body)
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
            data["choices"][0]["message"]  # noqa: B018 - 校验结构
            return data
        except (ValueError, KeyError, IndexError, TypeError):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析返回内容") from None

    @staticmethod
    def _cost(data: dict[str, Any]) -> float | None:
        cost = (data.get("usage") or {}).get("cost")
        return float(cost) if cost is not None else None

    @staticmethod
    def _citations(message: dict[str, Any]) -> list[dict[str, Any]]:
        out = []
        for item in message.get("annotations") or []:
            cite = item.get("url_citation") if isinstance(item, dict) else None
            if isinstance(cite, dict):
                out.append(cite)
        return out

    async def search(self, query: str, max_results: int) -> SearchResponse:
        data = await self._post(self._body(query, max_results))
        message = data["choices"][0]["message"]
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for cite in self._citations(message):
            url = str(cite.get("url") or "")
            if not url.startswith(("http://", "https://")) or url in seen:
                continue
            seen.add(url)
            hits.append(
                SearchHit(str(cite.get("title") or url), url, str(cite.get("content") or ""))
            )
        return SearchResponse(tuple(hits[:max_results]), self._cost(data))

    async def fetch(self, urls: list[str], max_chars: int) -> FetchResponse:
        if not self.supports_fetch:
            raise ProviderError(ErrorKind.BAD_REQUEST, self.name, "未开启读取网页")
        pages = []
        total = 0.0
        known = False
        for url in urls:
            body = {
                "model": self.model,
                "messages": self.render_fetch(url),
                "tools": [self.fetch_tool],
                # 正文按约 2 字 / token 估计输出上限
                "max_tokens": min(8000, max_chars // 2 + 200),
                "usage": {"include": True},
            }
            data = await self._post(body)
            cost = self._cost(data)
            if cost is not None:
                total += cost
                known = True
            message = data["choices"][0]["message"]
            text = str(message.get("content") or "").strip()
            # 有的引擎会把取回的正文放在注释里：比模型转述更可靠时优先使用
            direct = [
                str(c.get("content") or "")
                for c in self._citations(message)
                if str(c.get("url") or "").rstrip("/") == url.rstrip("/")
            ]
            if direct and len(max(direct, key=len)) > len(text):
                text = max(direct, key=len)
            ok = bool(text) and not text.startswith(FAILED) and len(text) >= 20
            pages.append(FetchedPage(url, text[:max_chars] if ok else "", ok=ok))
        return FetchResponse(tuple(pages), total if known else None)

    async def aclose(self) -> None:
        await self._client.aclose()


@register_search("openrouter")
def _factory(name: str, spec: SearchProviderSpec, key: Secret | None, timeout_s: float):
    return OpenRouterSearch(name, spec, key, timeout_s)
