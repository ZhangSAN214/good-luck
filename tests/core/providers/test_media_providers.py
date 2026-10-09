"""媒体能力：OpenAI 兼容适配器的语音 / 转写 / 视频接口格式，渠道路由的通用调用，Fake 的脚本。"""

from __future__ import annotations

import json

import httpx
import pytest

from roundtable.core.providers import (
    ErrorKind,
    FakeProvider,
    Media,
    ProviderError,
    Secret,
    UnsupportedCapability,
    VideoJob,
)
from roundtable.core.providers.fake import FAKE_PNG, FAKE_WAV
from roundtable.core.providers.openai_compat import OpenAICompatProvider

from .conftest import FAKE_KEYS, channel
from .test_router import make_router

KEY = FAKE_KEYS["OPENROUTER_API_KEY"]


def make(handler):
    return OpenAICompatProvider(
        "openrouter",
        channel(),
        Secret(KEY),
        30,
        transport=httpx.MockTransport(handler),
    )


async def test_speech_request_and_audio_bytes():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=b"ID3audio")

    out = await make(handler).synthesize_speech("tts-m", "你好", {"voice": "alloy"})
    assert seen["url"].endswith("/audio/speech")
    assert seen["body"] == {
        "model": "tts-m",
        "input": "你好",
        "response_format": "mp3",
        "voice": "alloy",
    }
    assert out.media.kind == "audio" and out.media.mime == "audio/mpeg"
    assert out.media.data == b"ID3audio" and out.cost_usd is None


async def test_transcribe_request_and_usage():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"text": "你好", "usage": {"cost": 0.0005, "seconds": 12.5}}
        )

    audio = Media("audio", "audio/wav", b"RIFFxxxx")
    out = await make(handler).transcribe("whisper-m", audio, {"language": "zh"})
    assert seen["url"].endswith("/audio/transcriptions")
    body = seen["body"]
    assert body["model"] == "whisper-m" and body["language"] == "zh"
    assert body["input_audio"]["format"] == "wav" and body["input_audio"]["data"]
    assert (out.text, out.cost_usd, out.seconds) == ("你好", 0.0005, 12.5)


async def test_video_submit_poll_fetch_cycle():
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.method == "POST":
            body = json.loads(request.content)
            assert body["model"] == "vid" and body["prompt"] == "海浪" and body["duration"] == 5
            return httpx.Response(
                202,
                json={"id": "job-1", "polling_url": "/api/v1/videos/job-1", "status": "pending"},
            )
        if request.url.path.endswith("/content"):
            return httpx.Response(200, content=b"mp4data", headers={"content-type": "video/mp4"})
        return httpx.Response(
            200,
            json={
                "id": "job-1",
                "status": "completed",
                "unsigned_urls": ["https://x/y"],
                "usage": {"cost": 0.4},
            },
        )

    p = make(handler)
    job = await p.submit_video("vid", "海浪", {"duration": 5})
    assert (job.job_id, job.state) == ("job-1", "pending")
    assert job.polling_url == "videos/job-1"  # 相对于 base_url
    done = await p.poll_video("vid", job)
    assert done.state == "completed" and done.cost_usd == 0.4 and done.content_urls
    media = await p.fetch_video("vid", done)
    assert media.kind == "video" and media.data == b"mp4data"
    assert calls[-1][2] == {"index": "0"}


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ("in_progress", "running"),
        ("failed", "failed"),
        ("expired", "expired"),
        ("weird", "running"),
    ],
)
async def test_video_status_mapping(status, state):
    def handler(request):
        return httpx.Response(200, json={"id": "j", "status": status, "error": "boom"})

    job = await make(handler).poll_video("vid", VideoJob("j"))
    assert job.state == state


async def test_media_http_errors_are_classified():
    def handler(request):
        return httpx.Response(402, json={"error": {"message": "no credits"}})

    with pytest.raises(ProviderError) as info:
        await make(handler).synthesize_speech("m", "x", {})
    assert info.value.kind == ErrorKind.QUOTA


async def test_unsupported_media_by_default():
    class Plain(FakeProvider):
        synthesize_speech = FakeProvider.__mro__[1].synthesize_speech
        submit_video = FakeProvider.__mro__[1].submit_video

    p = Plain("c")
    with pytest.raises(UnsupportedCapability):
        await p.synthesize_speech("m", "x", {})
    with pytest.raises(UnsupportedCapability):
        await p.submit_video("m", "x", {})


async def test_default_generate_image_uses_chat_images():
    from roundtable.core.providers import RawCompletion

    p = FakeProvider(
        "c", default=RawCompletion("", images=(Media("image", "image/png", FAKE_PNG),))
    )
    out = await FakeProvider.__mro__[1].generate_image(p, "m", "猫", {"modalities": ["image"]})
    assert out.images[0].data == FAKE_PNG
    assert p.calls[0].messages[0].content == "猫"


async def test_router_invoke_fails_over_and_skips_unsupported():
    class NoSpeech(FakeProvider):
        async def synthesize_speech(self, model, text, params):
            raise UnsupportedCapability("no")

    providers = {"google": NoSpeech("google"), "openrouter": FakeProvider("openrouter")}
    router, *_ = make_router(providers=providers)
    inv = await router.invoke(
        "gemini", lambda prov, route, model: prov.synthesize_speech(route.model, "x", {})
    )
    assert inv.channel == "openrouter" and inv.result.media.data == FAKE_WAV
    assert [a.channel for a in inv.attempts] == ["google", "openrouter"]
    assert [a.ok for a in inv.attempts] == [False, True]
