"""守卫：key 不得出现在异常信息、异常链/回溯、日志中。

假服务端故意在错误信息和响应头里回显 key（真实服务偶尔会部分回显），
并开启 DEBUG 日志（包括 httpx / SDK 的日志），检查所有输出。
"""

from __future__ import annotations

import logging
import traceback

import anthropic
import httpx
import httpx2
import pytest

from roundtable.core.config.schema import RequestPolicy
from roundtable.core.providers import (
    AllChannelsFailed,
    ChannelRouter,
    Message,
    ProviderError,
    Secret,
)
from roundtable.core.providers.anthropic_adapter import AnthropicProvider
from roundtable.core.providers.gemini import GeminiProvider
from roundtable.core.providers.openai_compat import OpenAICompatProvider

from .conftest import FAKE_KEYS, channel, models_config

MSG = [Message("user", "hi")]
STATUSES = [400, 401, 402, 403, 404, 429, 500, 503]


def echo_error(status: int, key: str) -> dict:
    return {
        "error": {
            "type": "authentication_error",
            "code": "invalid_api_key",
            "message": f"Incorrect API key provided: {key}. Authorization: Bearer {key}",
        }
    }


def build(adapter: str, key: str, handler):
    """handler(status_or_exc) → 返回该适配器使用的 httpx/httpx2 响应。"""
    if adapter == "anthropic":
        client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
        return AnthropicProvider(
            "anthropic", channel("anthropic"), Secret(key), 5, http_client=client
        )
    cls = {"openai_compat": OpenAICompatProvider, "gemini": GeminiProvider}[adapter]
    return cls(adapter, channel(adapter), Secret(key), 5, transport=httpx.MockTransport(handler))


ADAPTERS = [
    ("openai_compat", FAKE_KEYS["OPENAI_API_KEY"], httpx),
    ("gemini", FAKE_KEYS["GEMINI_API_KEY"], httpx),
    ("anthropic", FAKE_KEYS["ANTHROPIC_API_KEY"], httpx2),
]


def all_output(exc: BaseException, caplog) -> str:
    return "\n".join([str(exc), repr(exc), "".join(traceback.format_exception(exc)), caplog.text])


@pytest.fixture(autouse=True)
def debug_logging(caplog):
    caplog.set_level(logging.DEBUG)


@pytest.mark.parametrize("adapter, key, http", ADAPTERS)
@pytest.mark.parametrize("status", STATUSES)
async def test_http_error_does_not_leak_key(adapter, key, http, status, caplog):
    def handler(request):
        return http.Response(
            status, json=echo_error(status, key), headers={"x-echo": key, "retry-after": "1"}
        )

    with pytest.raises(ProviderError) as info:
        await build(adapter, key, handler).complete("m", MSG, {})
    assert key not in all_output(info.value, caplog)


@pytest.mark.parametrize("adapter, key, http", ADAPTERS)
@pytest.mark.parametrize("exc_name", ["ConnectError", "ReadTimeout", "ProxyError"])
async def test_transport_error_does_not_leak_key(adapter, key, http, exc_name, caplog):
    def handler(request):
        raise getattr(http, exc_name)(f"failed for key {key}")

    with pytest.raises(ProviderError) as info:
        await build(adapter, key, handler).complete("m", MSG, {})
    assert key not in all_output(info.value, caplog)


@pytest.mark.parametrize("adapter, key, http", ADAPTERS)
async def test_success_logs_do_not_leak_key(adapter, key, http, caplog):
    bodies = {
        "openai_compat": {"choices": [{"message": {"content": "ok"}}], "usage": {}},
        "gemini": {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]},
        "anthropic": {
            "id": "m",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    }
    raw = await build(adapter, key, lambda r: http.Response(200, json=bodies[adapter])).complete(
        "m", MSG, {}
    )
    assert raw.text == "ok"
    assert key not in caplog.text


async def test_router_failover_trail_does_not_leak_keys(caplog):
    """三个真实适配器串成一条渠道链，全部失败；检查路由日志和最终异常。"""
    cfg = models_config(
        {"openai_compat": "direct", "gemini": "direct", "anthropic": "aggregator"},
        {"m": ["openai_compat", "gemini", "anthropic"]},
    )
    providers = {}
    for adapter, key, http in ADAPTERS:

        def handler(request, key=key, http=http):
            return http.Response(429, json=echo_error(429, key), headers={"x-echo": key})

        providers[adapter] = build(adapter, key, handler)

    router = ChannelRouter(cfg, providers, policy=RequestPolicy(failover_rounds=1))
    with pytest.raises(AllChannelsFailed) as info:
        await router.complete("m", MSG)
    output = all_output(info.value, caplog)
    assert len(info.value.attempts) == 3
    for _, key, _ in ADAPTERS:
        assert key not in output
