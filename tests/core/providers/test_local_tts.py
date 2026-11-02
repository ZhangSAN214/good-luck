"""本地配音适配器：假 HTTP（httpx.MockTransport），不联网、不需要本地服务。"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from roundtable.core.providers import (
    ErrorKind,
    Message,
    ProviderError,
    UnsupportedCapability,
    adapter_names,
)
from roundtable.core.providers.fake import FAKE_WAV
from roundtable.core.providers.local_tts import (
    LocalTTSProvider,
    dig,
    fill_fields,
    last_sse_json,
    sniff_audio,
)

from .conftest import channel


def provider(handler) -> LocalTTSProvider:
    spec = channel("local_tts", "local", base_url="http://127.0.0.1:7860", key_env=None)
    return LocalTTSProvider("local_tts", spec, None, 30, transport=httpx.MockTransport(handler))


def test_registered_and_helpers():
    assert "local_tts" in adapter_names()
    assert sniff_audio(FAKE_WAV) == "audio/wav" and sniff_audio(b"ID3....") == "audio/mpeg"
    assert sniff_audio(b"hello") is None
    assert fill_fields({"t": "说：{{text}}", "v": "{{voice}}", "n": 3}, "你好", "rt-a") == {
        "t": "说：你好",
        "v": "rt-a",
        "n": 3,
    }
    assert dig({"a": [{"b": "x"}]}, "a.0.b") == "x" and dig({"a": 1}, "a.b") is None
    stream = 'event: progress\ndata: {"step": 1}\n\ndata: not json\n\ndata: {"url": "/o.wav"}\n\n'
    assert last_sse_json(stream) == {"url": "/o.wav"}


async def test_raw_audio_response_with_default_json_fields():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, content=FAKE_WAV, headers={"content-type": "audio/wav"})

    p = provider(handler)
    out = await p.synthesize_speech(
        "local", "你好呀", {"voice": "rt-openai", "response_format": "mp3"}
    )
    assert seen == [("/tts", {"text": "你好呀", "voice": "rt-openai"})]
    assert out.media.kind == "audio" and out.media.mime == "audio/wav" and out.cost_usd == 0.0
    with pytest.raises(UnsupportedCapability):
        await p.complete("local", [Message("user", "hi")], {})
    await p.aclose()


async def test_form_request_and_sse_stream_with_file_url():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/tts":
            seen["form"] = request.content.decode()
            body = 'data: {"step": "gen"}\n\ndata: {"result": {"file": "/outputs/a.wav"}}\n\n'
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        assert request.url.path == "/outputs/a.wav"
        return httpx.Response(200, content=FAKE_WAV, headers={"content-type": "application/x"})

    params = {
        "voice": "rt-xai",
        "path": "/api/v1/tts",
        "request": "form",
        "fields": {"text": "{{text}}", "preset": "{{voice}}", "emo_weight": 0.6},
        "url_field": "result.file",
    }
    out = await provider(handler).synthesize_speech("local", "早", params)
    assert "preset=rt-xai" in seen["form"] and "emo_weight=0.6" in seen["form"]
    assert out.media.mime == "audio/wav" and out.media.data == FAKE_WAV


async def test_json_with_base64_audio():
    encoded = base64.b64encode(FAKE_WAV).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"audio": "data:audio/wav;base64," + encoded}})

    out = await provider(handler).synthesize_speech(
        "local", "嗯", {"voice": "v", "audio_field": "data.audio"}
    )
    assert out.media.data == FAKE_WAV


async def test_service_down_errors_and_missing_audio():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError) as exc:
        await provider(down).synthesize_speech("local", "嗯", {"voice": "v"})
    assert exc.value.kind == ErrorKind.NETWORK and exc.value.__cause__ is None

    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="CUDA out of memory " + "x" * 500)

    with pytest.raises(ProviderError) as exc:
        await provider(boom).synthesize_speech("local", "嗯", {"voice": "v"})
    assert exc.value.kind == ErrorKind.SERVER and len(str(exc.value)) < 400

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    with pytest.raises(ProviderError) as exc:
        await provider(empty).synthesize_speech("local", "嗯", {"voice": "v"})
    assert exc.value.kind == ErrorKind.INVALID_RESPONSE
