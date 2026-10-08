"""OpenAI 兼容适配器：用 httpx.MockTransport 模拟服务端，不联网。"""

from __future__ import annotations

import json

import httpx
import pytest

from roundtable.core.providers import ErrorKind, Message, ProviderError, Secret
from roundtable.core.providers.openai_compat import OpenAICompatProvider

from .conftest import FAKE_KEYS, channel

KEY = FAKE_KEYS["OPENROUTER_API_KEY"]
MESSAGES = [Message("system", "be brief"), Message("user", "1+1?")]


def make(handler, *, key=KEY, **spec_kw):
    return OpenAICompatProvider(
        "openrouter",
        channel(**spec_kw),
        Secret(key) if key else None,
        30,
        transport=httpx.MockTransport(handler),
    )


def ok_body(**usage):
    return {
        "choices": [{"message": {"role": "assistant", "content": "2"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3, **usage},
    }


async def test_request_shape_and_parse():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json=ok_body(cost=0.0021, prompt_tokens_details={"cached_tokens": 4})
        )

    provider = make(handler, extra_body={"usage": {"include": True}})
    raw = await provider.complete("vendor/m", MESSAGES, {"temperature": 0.3})
    assert seen["url"] == "https://api.example.test/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["model"] == "vendor/m"
    assert seen["body"]["temperature"] == 0.3
    assert seen["body"]["usage"] == {"include": True}
    assert seen["body"]["messages"][0] == {"role": "system", "content": "be brief"}
    assert (raw.text, raw.input_tokens, raw.output_tokens, raw.cached_tokens) == ("2", 12, 3, 4)
    assert raw.reported_cost_usd == pytest.approx(0.0021)
    await provider.aclose()


async def test_no_key_sends_no_auth_header():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=ok_body())

    raw = await make(handler, key=None, kind="local").complete("m", MESSAGES, {})
    assert seen["auth"] is None and raw.reported_cost_usd is None


async def test_params_cannot_override_model_or_messages():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    await make(handler).complete("real", MESSAGES, {"model": "other", "messages": []})
    assert seen["body"]["model"] == "real" and len(seen["body"]["messages"]) == 2


@pytest.mark.parametrize(
    "status, body, kind",
    [
        (429, {"error": {"message": "slow down"}}, ErrorKind.RATE_LIMIT),
        (429, {"error": {"code": "insufficient_quota", "message": "quota"}}, ErrorKind.QUOTA),
        (402, {"error": {"message": "Insufficient credits"}}, ErrorKind.QUOTA),
        (401, {"error": {"message": "bad key"}}, ErrorKind.AUTH),
        (404, {"error": {"message": "no such model"}}, ErrorKind.NOT_FOUND),
        (503, "<html>down</html>", ErrorKind.SERVER),
        (400, {"error": {"message": "bad param"}}, ErrorKind.BAD_REQUEST),
    ],
)
async def test_http_errors(status, body, kind):
    def handler(request):
        if isinstance(body, str):
            return httpx.Response(status, text=body, headers={"retry-after": "5"})
        return httpx.Response(status, json=body, headers={"retry-after": "5"})

    with pytest.raises(ProviderError) as info:
        await make(handler).complete("m", MESSAGES, {})
    assert info.value.kind == kind
    assert info.value.status == status
    assert info.value.retry_after == 5.0
    assert info.value.channel == "openrouter"


@pytest.mark.parametrize(
    "exc, kind",
    [
        (httpx.ConnectError("refused"), ErrorKind.NETWORK),
        (httpx.ProxyError("proxy 403"), ErrorKind.NETWORK),
        (httpx.ReadTimeout("slow"), ErrorKind.TIMEOUT),
    ],
)
async def test_transport_errors(exc, kind):
    def handler(request):
        raise exc

    with pytest.raises(ProviderError) as info:
        await make(handler).complete("m", MESSAGES, {})
    assert info.value.kind == kind


@pytest.mark.parametrize("payload", [{"choices": []}, {"nope": 1}, "not json"])
async def test_unparseable_response(payload):
    def handler(request):
        if isinstance(payload, str):
            return httpx.Response(200, text=payload)
        return httpx.Response(200, json=payload)

    with pytest.raises(ProviderError) as info:
        await make(handler).complete("m", MESSAGES, {})
    assert info.value.kind == ErrorKind.INVALID_RESPONSE


async def test_content_filter_is_refusal():
    def handler(request):
        body = ok_body()
        body["choices"][0]["finish_reason"] = "content_filter"
        return httpx.Response(200, json=body)

    with pytest.raises(ProviderError) as info:
        await make(handler).complete("m", MESSAGES, {})
    assert info.value.kind == ErrorKind.REFUSAL


async def test_media_capabilities_not_supported():
    from roundtable.core.providers import UnsupportedCapability

    provider = make(lambda r: httpx.Response(200, json=ok_body()))
    with pytest.raises(UnsupportedCapability):
        await provider.generate_image("m", "cat", {})
    with pytest.raises(UnsupportedCapability):
        await provider.synthesize_speech("m", "hi", {})
    with pytest.raises(UnsupportedCapability):
        await provider.transcribe("m", b"", {})


async def test_param_aliases_rename_parameters():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    provider = make(handler, param_aliases={"max_tokens": "max_completion_tokens"})
    await provider.complete("m", MESSAGES, {"max_tokens": 100, "temperature": 0.2})
    assert seen["body"]["max_completion_tokens"] == 100
    assert "max_tokens" not in seen["body"] and seen["body"]["temperature"] == 0.2


def test_repo_openai_channel_renames_max_tokens():
    from roundtable.core.config import load_config

    channels = load_config().models.channels
    assert channels["openai"].param_aliases == {"max_tokens": "max_completion_tokens"}
    assert channels["openrouter"].param_aliases == {}
