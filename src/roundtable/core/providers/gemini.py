"""Google Gemini 官方接口（generateContent）。key 通过请求头传递，不出现在 URL 中。"""

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

# 通用参数名 → Gemini generationConfig 字段；其余参数原样放进 generationConfig
_PARAM_MAP = {"max_tokens": "maxOutputTokens", "temperature": "temperature", "top_p": "topP"}
_BLOCKED = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"})


@register_adapter("gemini")
class GeminiProvider(Provider):
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
            headers["x-goog-api-key"] = key.reveal()
        self._secrets = [key.reveal()] if key else []
        self._extra_body = dict(spec.extra_body)
        self._client = httpx.AsyncClient(
            base_url=spec.base_url, headers=headers, timeout=timeout_s, transport=transport
        )

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.channel, safe_detail(detail, self._secrets), **kw)

    @staticmethod
    def _body(messages: Sequence[Message], params: dict[str, Any]) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            {"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]}
            for m in messages
            if m.role != "system"
        ]
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        config = {_PARAM_MAP.get(k, k): v for k, v in params.items()}
        if config:
            body["generationConfig"] = config
        return body

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        body = {**self._body(messages, params), **self._extra_body}
        try:
            response = await self._client.post(f"models/{model}:generateContent", json=body)
        except httpx.TimeoutException:
            raise self._error(ErrorKind.TIMEOUT) from None
        except httpx.TransportError as exc:
            raise self._error(ErrorKind.NETWORK, type(exc).__name__) from None

        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            code, message = error_fields(payload)
            kind = kind_for_status(response.status_code, code)
            if _invalid_key(payload):
                kind = ErrorKind.AUTH
            raise self._error(
                kind,
                message,
                status=response.status_code,
                retry_after=retry_after(response.headers),
            ) from None

        try:
            data = response.json()
            block = (data.get("promptFeedback") or {}).get("blockReason")
            candidates = data.get("candidates") or []
            usage = data.get("usageMetadata") or {}
            if block or not candidates:
                raise self._error(ErrorKind.REFUSAL, f"提示被拦截：{block}" if block else "无候选")
            candidate = candidates[0]
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
            result = RawCompletion(
                text=text,
                input_tokens=int(usage.get("promptTokenCount") or 0),
                # 思考 token 按输出计费
                output_tokens=int(usage.get("candidatesTokenCount") or 0)
                + int(usage.get("thoughtsTokenCount") or 0),
                cached_tokens=int(usage.get("cachedContentTokenCount") or 0),
                truncated=candidate.get("finishReason") == "MAX_TOKENS",
            )
        except ProviderError:
            raise
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析返回内容") from None

        if candidate.get("finishReason") in _BLOCKED:
            raise self._error(ErrorKind.REFUSAL, f"finishReason={candidate['finishReason']}")
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def _invalid_key(payload: Any) -> bool:
    """Gemini 对无效 key 返回 400，原因写在 error.details[].reason 中。"""
    if not isinstance(payload, dict):
        return False
    details = (payload.get("error") or {}).get("details") or []
    return any(isinstance(d, dict) and d.get("reason") == "API_KEY_INVALID" for d in details)
