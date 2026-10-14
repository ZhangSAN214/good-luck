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


async def test_images_and_audio_as_content_parts():
    import base64

    from roundtable.core.providers import Media

    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    media = (Media("image", "image/png", b"PNGDATA", "a.png"), Media("audio", "audio/mpeg", b"MP3"))
    msgs = [Message("system", "s"), Message("user", "看图", media)]
    await make(handler).complete("m", msgs, {})
    user = seen["body"]["messages"][1]
    assert user["content"][0] == {"type": "text", "text": "看图"}
    url = "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()
    assert user["content"][1] == {"type": "image_url", "image_url": {"url": url}}
    audio = {"data": base64.b64encode(b"MP3").decode(), "format": "mp3"}
    assert user["content"][2] == {"type": "input_audio", "input_audio": audio}
    assert seen["body"]["messages"][0] == {"role": "system", "content": "s"}  # 纯文字保持原格式


def test_media_bytes_not_in_repr():
    from roundtable.core.providers import Media

    m = Media("image", "image/png", b"SECRET-IMAGE-BYTES")
    assert "SECRET-IMAGE-BYTES" not in repr(Message("user", "q", (m,)))
    assert m.describe()["bytes"] == 18 and "data" not in m.describe()


async def test_image_output_parsed_from_data_uri():
    import base64

    url = "data:image/png;base64," + base64.b64encode(b"PNGBYTES").decode()

    def handler(request: httpx.Request):
        body = ok_body()
        body["choices"][0]["message"]["images"] = [
            {"type": "image_url", "image_url": {"url": url}},
            {"type": "image_url", "image_url": {"url": "https://not-a-data-uri"}},
        ]
        return httpx.Response(200, json=body)

    raw = await make(handler).complete("m", MESSAGES, {"modalities": ["image", "text"]})
    assert [(m.mime, m.data) for m in raw.images] == [("image/png", b"PNGBYTES")]


async def test_finish_reason_and_reasoning_tokens_are_parsed():
    def handler(request):
        body = ok_body(completion_tokens_details={"reasoning_tokens": 3900})
        body["choices"][0]["finish_reason"] = "length"
        return httpx.Response(200, json=body)

    raw = await make(handler).complete("m", MESSAGES, {})
    assert (raw.finish_reason, raw.truncated, raw.reasoning_tokens) == ("length", True, 3900)


@pytest.mark.parametrize(
    "spec_kw, expected",
    [
        ({"kind": "aggregator"}, {"reasoning": {"max_tokens": 800}}),
        ({"kind": "aggregator", "reasoning": "effort"}, {"reasoning_effort": "low"}),
        ({"kind": "direct"}, {}),  # 未声明：直连渠道忽略思考参数，不报错
        ({"kind": "direct", "reasoning": "effort"}, {"reasoning_effort": "low"}),
        ({"kind": "aggregator", "reasoning": "none"}, {}),
    ],
)
async def test_neutral_reasoning_param_translated_per_channel(spec_kw, expected):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    params = {"reasoning": {"max_tokens": 800}}
    if expected.get("reasoning_effort"):
        params = {"reasoning": {"effort": "low"}}
    await make(handler, **spec_kw).complete("m", MESSAGES, params)
    body = seen["body"]
    assert {k: body[k] for k in ("reasoning", "reasoning_effort") if k in body} == expected
