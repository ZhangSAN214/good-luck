"""Gemini 官方接口适配器。"""

from __future__ import annotations

import json

import httpx
import pytest

from roundtable.core.providers import ErrorKind, Message, ProviderError, Secret
from roundtable.core.providers.gemini import GeminiProvider

from .conftest import FAKE_KEYS, channel

KEY = FAKE_KEYS["GEMINI_API_KEY"]
MESSAGES = [
    Message("system", "be brief"),
    Message("user", "q1"),
    Message("assistant", "a1"),
    Message("user", "q2"),
]


def make(handler):
    spec = channel("gemini", base_url="https://gl.example.test/v1beta", key_env="GEMINI_API_KEY")
    return GeminiProvider("google", spec, Secret(KEY), 30, transport=httpx.MockTransport(handler))


def ok_body(**extra):
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": "think", "thought": True}, {"text": "answer"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 5,
            "thoughtsTokenCount": 7,
            "cachedContentTokenCount": 8,
        },
        **extra,
    }


async def test_request_shape_and_parse():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    raw = await make(handler).complete("gemini-x", MESSAGES, {"max_tokens": 100, "top_p": 0.9})
    assert seen["url"] == "https://gl.example.test/v1beta/models/gemini-x:generateContent"
    assert KEY not in seen["url"], "key 不应出现在 URL 中"
    assert seen["headers"]["x-goog-api-key"] == KEY
    body = seen["body"]
    assert body["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert [c["role"] for c in body["contents"]] == ["user", "model", "user"]
    assert body["generationConfig"] == {"maxOutputTokens": 100, "topP": 0.9}
    assert raw.text == "answer"  # 思考部分不计入正文
    assert (raw.input_tokens, raw.output_tokens, raw.cached_tokens) == (20, 12, 8)


@pytest.mark.parametrize(
    "status, body, kind",
    [
        (
            429,
            {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}},
            ErrorKind.RATE_LIMIT,
        ),
        (
            400,
            {
                "error": {
                    "status": "INVALID_ARGUMENT",
                    "message": "API key not valid",
                    "details": [{"reason": "API_KEY_INVALID"}],
                }
            },
            ErrorKind.AUTH,
        ),
        (400, {"error": {"status": "INVALID_ARGUMENT", "message": "bad"}}, ErrorKind.BAD_REQUEST),
        (404, {"error": {"message": "model not found"}}, ErrorKind.NOT_FOUND),
        (500, {}, ErrorKind.SERVER),
    ],
)
async def test_http_errors(status, body, kind):
    with pytest.raises(ProviderError) as info:
        await make(lambda r: httpx.Response(status, json=body)).complete("m", MESSAGES, {})
    assert info.value.kind == kind


@pytest.mark.parametrize(
    "body",
    [
        {"promptFeedback": {"blockReason": "SAFETY"}},
        {"candidates": []},
        {"candidates": [{"content": {"parts": []}, "finishReason": "SAFETY"}]},
    ],
)
async def test_blocked_is_refusal(body):
    with pytest.raises(ProviderError) as info:
        await make(lambda r: httpx.Response(200, json=body)).complete("m", MESSAGES, {})
    assert info.value.kind == ErrorKind.REFUSAL


async def test_network_error():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError) as info:
        await make(handler).complete("m", MESSAGES, {})
    assert info.value.kind == ErrorKind.NETWORK


async def test_unparseable():
    with pytest.raises(ProviderError) as info:
        await make(lambda r: httpx.Response(200, text="oops")).complete("m", MESSAGES, {})
    assert info.value.kind == ErrorKind.INVALID_RESPONSE


async def test_media_as_inline_data():
    import base64

    from roundtable.core.providers import Media

    seen = {}

    def handler(request: httpx.Request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=ok_body())

    media = (Media("image", "image/png", b"PNG"), Media("audio", "audio/wav", b"WAV"))
    await make(handler).complete("gemini-x", [Message("user", "q", media)], {})
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "q"}
    assert parts[1] == {
        "inline_data": {"mime_type": "image/png", "data": base64.b64encode(b"PNG").decode()}
    }
    assert parts[2]["inline_data"]["mime_type"] == "audio/wav"


async def test_image_output_inline_data():
    import base64

    def handler(request: httpx.Request):
        body = ok_body()
        body["candidates"][0]["content"]["parts"].append(
            {"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(b"JPG").decode()}}
        )
        return httpx.Response(200, json=body)

    raw = await make(handler).complete("gemini-x", MESSAGES, {})
    assert [(m.mime, m.data) for m in raw.images] == [("image/jpeg", b"JPG")]
    assert raw.text == "answer"
