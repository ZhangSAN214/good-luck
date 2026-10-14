"""Anthropic 官方 SDK 适配器：通过 httpx2.MockTransport 注入假服务端。"""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from roundtable.core.config import ConfigError
from roundtable.core.providers import ErrorKind, Message, ProviderError, Secret
from roundtable.core.providers.anthropic_adapter import DEFAULT_MAX_TOKENS, AnthropicProvider

from .conftest import FAKE_KEYS, channel

KEY = FAKE_KEYS["ANTHROPIC_API_KEY"]
MESSAGES = [Message("system", "be brief"), Message("user", "1+1?")]
SPEC = channel("anthropic", base_url="https://anthropic.example.test", key_env="ANTHROPIC_API_KEY")


def make(handler):
    client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler))
    return AnthropicProvider("anthropic", SPEC, Secret(KEY), 30, http_client=client)


def ok_body(stop_reason="end_turn"):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-test",
        "content": [{"type": "text", "text": "2"}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": 10,
            "output_tokens": 3,
            "cache_read_input_tokens": 5,
            "cache_creation_input_tokens": 2,
        },
    }


def error_body(err_type, message="m"):
    return {"type": "error", "error": {"type": err_type, "message": message}}


async def test_request_shape_and_parse():
    seen = {}

    def handler(request: httpx2.Request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers.get("x-api-key")
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=ok_body())

    provider = make(handler)
    raw = await provider.complete("claude-test", MESSAGES, {})
    assert seen["url"] == "https://anthropic.example.test/v1/messages"
    assert seen["key"] == KEY
    assert seen["body"]["system"] == "be brief"
    assert seen["body"]["messages"] == [{"role": "user", "content": "1+1?"}]
    assert seen["body"]["max_tokens"] == DEFAULT_MAX_TOKENS
    assert raw.text == "2"
    # 统一为"含缓存"的输入口径：10 + 5 + 2
    assert (raw.input_tokens, raw.output_tokens, raw.cached_tokens) == (17, 3, 5)
    await provider.aclose()


async def test_max_tokens_param_used():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=ok_body())

    await make(handler).complete("m", MESSAGES, {"max_tokens": 512})
    assert seen["body"]["max_tokens"] == 512


@pytest.mark.parametrize(
    "status, err_type, kind",
    [
        (429, "rate_limit_error", ErrorKind.RATE_LIMIT),
        (402, "billing_error", ErrorKind.QUOTA),
        (401, "authentication_error", ErrorKind.AUTH),
        (403, "permission_error", ErrorKind.AUTH),
        (404, "not_found_error", ErrorKind.NOT_FOUND),
        (529, "overloaded_error", ErrorKind.SERVER),
        (500, "api_error", ErrorKind.SERVER),
        (400, "invalid_request_error", ErrorKind.BAD_REQUEST),
    ],
)
async def test_http_errors(status, err_type, kind):
    def handler(request):
        return httpx2.Response(status, json=error_body(err_type), headers={"retry-after": "3"})

    with pytest.raises(ProviderError) as info:
        await make(handler).complete("m", MESSAGES, {})
    assert info.value.kind == kind
    assert info.value.status == status
    assert info.value.retry_after == 3.0


async def test_network_error():
    def handler(request):
        raise httpx2.ConnectError("down")

    with pytest.raises(ProviderError) as info:
        await make(handler).complete("m", MESSAGES, {})
    assert info.value.kind == ErrorKind.NETWORK


async def test_timeout():
    def handler(request):
        raise httpx2.ReadTimeout("slow")

    with pytest.raises(ProviderError) as info:
        await make(handler).complete("m", MESSAGES, {})
    assert info.value.kind == ErrorKind.TIMEOUT


async def test_refusal():
    with pytest.raises(ProviderError) as info:
        await make(lambda r: httpx2.Response(200, json=ok_body("refusal"))).complete(
            "m", MESSAGES, {}
        )
    assert info.value.kind == ErrorKind.REFUSAL


def test_requires_key():
    # 不允许 SDK 回退到环境变量或本机登录凭据
    with pytest.raises(ConfigError):
        AnthropicProvider("anthropic", SPEC, None, 30)


async def test_image_blocks_and_audio_rejected():
    import base64

    from roundtable.core.providers import Media

    seen = {}

    def handler(request: httpx2.Request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=ok_body())

    provider = make(handler)
    image = Media("image", "image/jpeg", b"JPG", "a.jpg")
    await provider.complete("claude-test", [Message("user", "看图", (image,))], {})
    content = seen["body"]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "看图"}
    source = {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": base64.b64encode(b"JPG").decode(),
    }
    assert content[1] == {"type": "image", "source": source}

    audio = Media("audio", "audio/mpeg", b"MP3")
    with pytest.raises(ProviderError) as info:
        await provider.complete("claude-test", [Message("user", "听", (audio,))], {})
    assert info.value.kind == ErrorKind.BAD_REQUEST  # 不切换渠道（换了也是同样的请求）
    await provider.aclose()
