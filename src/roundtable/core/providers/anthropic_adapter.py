"""Anthropic 官方直连，使用官方 Python SDK。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import anthropic

from roundtable.core.config import ConfigError
from roundtable.core.config.schema import ChannelSpec

from ._http import kind_for_status, retry_after, safe_detail
from .base import Message, Provider, RawCompletion
from .errors import ErrorKind, ProviderError
from .registry import register_adapter
from .secrets import Secret

DEFAULT_MAX_TOKENS = 16000


@register_adapter("anthropic")
class AnthropicProvider(Provider):
    def __init__(
        self,
        channel: str,
        spec: ChannelSpec,
        key: Secret | None,
        timeout_s: float,
        http_client: anthropic.DefaultAsyncHttpxClient | None = None,
    ) -> None:
        super().__init__(channel)
        if key is None:
            # 不允许 SDK 回退到环境变量或本机登录凭据：key 只能来自 .env 配置的变量
            raise ConfigError(f"渠道 {channel} 使用 anthropic 适配器，必须配置 key_env")
        self._secrets = [key.reveal()]
        self._extra_body = dict(spec.extra_body)
        self._client = anthropic.AsyncAnthropic(
            api_key=key.reveal(),
            base_url=spec.base_url,
            timeout=timeout_s,
            max_retries=0,  # 重试与切换由 ChannelRouter 负责
            http_client=http_client,
        )

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.channel, safe_detail(detail, self._secrets), **kw)

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        params = dict(params)
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": params.pop("max_tokens", DEFAULT_MAX_TOKENS),
            "messages": [
                {"role": m.role, "content": m.content} for m in messages if m.role != "system"
            ],
            **params,
        }
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        if system:
            kwargs["system"] = system
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body

        try:
            response = await self._client.messages.create(**kwargs)
        except anthropic.APITimeoutError:  # 必须在 APIConnectionError 之前
            raise self._error(ErrorKind.TIMEOUT) from None
        except anthropic.APIConnectionError as exc:
            raise self._error(ErrorKind.NETWORK, type(exc).__name__) from None
        except anthropic.APIStatusError as exc:
            kind = kind_for_status(exc.status_code, getattr(exc, "type", None))
            raise self._error(
                kind,
                getattr(exc, "message", "") or "",
                status=exc.status_code,
                retry_after=retry_after(exc.response.headers),
            ) from None

        if response.stop_reason == "refusal":
            raise self._error(ErrorKind.REFUSAL, "stop_reason=refusal")
        try:
            text = "".join(b.text for b in response.content if b.type == "text")
            usage = response.usage
            cache_read = usage.cache_read_input_tokens or 0
            cache_write = usage.cache_creation_input_tokens or 0
        except (AttributeError, TypeError):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析返回内容") from None
        return RawCompletion(
            text=text,
            # Anthropic 的 input_tokens 不含缓存部分，这里统一为"含缓存"的口径
            input_tokens=usage.input_tokens + cache_read + cache_write,
            output_tokens=usage.output_tokens,
            cached_tokens=cache_read,
            truncated=response.stop_reason == "max_tokens",
        )

    async def aclose(self) -> None:
        await self._client.close()
