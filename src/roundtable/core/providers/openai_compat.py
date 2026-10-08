"""OpenAI 兼容接口（/chat/completions）。用于 OpenRouter、OpenAI、xAI、DeepSeek、本地端点。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from roundtable.core.config.schema import ChannelSpec

from ._http import error_fields, kind_for_status, retry_after, safe_detail
from .base import Message, Provider, RawCompletion
from .errors import ErrorKind, ProviderError
from .registry import register_adapter
from .secrets import Secret


@register_adapter("openai_compat")
class OpenAICompatProvider(Provider):
    def __init__(
        self,
        channel: str,
        spec: ChannelSpec,
        key: Secret | None,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(channel)
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Authorization"] = f"Bearer {key.reveal()}"
        self._secrets = [key.reveal()] if key else []
        self._extra_body = dict(spec.extra_body)
        self._aliases = dict(spec.param_aliases)
        self._client = httpx.AsyncClient(
            base_url=spec.base_url, headers=headers, timeout=timeout_s, transport=transport
        )

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.channel, safe_detail(detail, self._secrets), **kw)

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        params = {self._aliases.get(k, k): v for k, v in params.items()}
        body = {
            **params,
            **self._extra_body,
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
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
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
            usage = data.get("usage") or {}
            details = usage.get("prompt_tokens_details") or {}
            result = RawCompletion(
                text=text if isinstance(text, str) else str(text),
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                cached_tokens=int(details.get("cached_tokens") or 0),
                reported_cost_usd=_float_or_none(usage.get("cost")),
                truncated=choice.get("finish_reason") == "length",
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析返回内容") from None

        if choice.get("finish_reason") == "content_filter":
            raise self._error(ErrorKind.REFUSAL, "内容被过滤")
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
