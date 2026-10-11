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


# --- 画图接口：对话接口 vs /images（gpt-image 系列只能走后者）-------------------------------


async def test_images_api_request_and_b64_response():
    import base64

    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        data = {
            "data": [{"b64_json": base64.b64encode(FAKE_PNG).decode()}],
            "usage": {"cost": 0.04, "input_tokens": 12, "output_tokens": 1056},
        }
        return httpx.Response(200, json=data)

    ref = Media("image", "image/png", b"ref-bytes", "ref.png")
    out = await make(handler).generate_image(
        "openai/gpt-image-2",
        "四格人物卡",
        {"modalities": ["image", "text"], "quality": "low"},
        images=[ref],
        api="images",
    )
    assert seen["url"].endswith("/images") and "chat/completions" not in seen["url"]
    body = seen["body"]
    assert body["model"] == "openai/gpt-image-2" and body["prompt"] == "四格人物卡"
    assert "modalities" not in body and body["quality"] == "low"  # 对话接口专用的参数不带
    # OpenRouter /images 的参考图字段是 input_references（不是 images）
    assert "images" not in body
    ref_part = body["input_references"][0]
    assert ref_part["type"] == "image_url"
    url = ref_part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"ref-bytes"
    assert out.images[0].data == FAKE_PNG and out.images[0].mime == "image/png"
    assert (out.cost_usd, out.input_tokens, out.output_tokens) == (0.04, 12, 1056)


async def test_images_api_without_references_and_url_response():
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        if request.url.path.endswith("/images"):
            body = json.loads(request.content)
            assert "images" not in body and "input_references" not in body
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/x.png"}]})
        return httpx.Response(200, content=FAKE_PNG)

    out = await make(handler).generate_image("m", "猫", {}, api="images")
    assert out.images[0].data == FAKE_PNG and seen[-1] == "https://cdn.example/x.png"


async def test_images_api_uses_returned_media_type():
    import base64

    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"

    def handler(request: httpx.Request):
        data = {
            "created": 1748372400,
            "data": [{"b64_json": base64.b64encode(jpeg).decode(), "media_type": "image/webp"}],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 4175,
                "total_tokens": 4175,
                "cost": 0.04,
            },
        }
        return httpx.Response(200, json=data)

    out = await make(handler).generate_image("openai/gpt-image-1-mini", "猫", {}, api="images")
    assert out.images[0].data == jpeg and out.images[0].mime == "image/webp"
    assert (out.cost_usd, out.input_tokens, out.output_tokens) == (0.04, 0, 4175)


async def test_images_api_errors_are_classified_and_empty_result_is_invalid():
    def not_found(request):
        return httpx.Response(404, json={"error": {"message": "no such model"}})

    with pytest.raises(ProviderError) as info:
        await make(not_found).generate_image("m", "猫", {}, api="images")
    assert info.value.kind == ErrorKind.NOT_FOUND

    def empty(request):
        return httpx.Response(200, json={"data": []})

    with pytest.raises(ProviderError) as info:
        await make(empty).generate_image("m", "猫", {}, api="images")
    assert info.value.kind == ErrorKind.INVALID_RESPONSE


async def test_chat_api_still_goes_through_chat_completions():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        data = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "images": [{"image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}],
                    }
                }
            ]
        }
        return httpx.Response(200, json=data)

    out = await make(handler).generate_image("g", "猫", {"modalities": ["image"]}, api="chat")
    assert seen["url"].endswith("/chat/completions") and out.images


async def test_providers_without_an_images_endpoint_refuse_it():
    p = FakeProvider("c")
    with pytest.raises(UnsupportedCapability):
        await FakeProvider.__mro__[1].generate_image(p, "m", "猫", {}, api="images")
