"""OpenAI 兼容适配器的边界与错误路径：流式里的杂行、图像接口的各种返回、视频任务的
轮询地址、余额接口的奇怪数据。全部用 httpx.MockTransport，不联网。"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from roundtable.core.providers import ErrorKind, Message, ProviderError, Secret
from roundtable.core.providers.base import Media, VideoJob
from roundtable.core.providers.fake import FAKE_PNG
from roundtable.core.providers.openai_compat import (
    OpenAICompatProvider,
    _float_or_none,
    _money,
    _sniff_image_mime,
)

from .conftest import FAKE_KEYS, channel

KEY = FAKE_KEYS["OPENROUTER_API_KEY"]
MESSAGES = [Message("user", "hi")]


def make(handler, **spec_kw):
    return OpenAICompatProvider(
        "openrouter", channel(**spec_kw), Secret(KEY), 30, transport=httpx.MockTransport(handler)
    )


def sse(*items) -> str:
    out = []
    for item in items:
        out.append(item if isinstance(item, str) else f"data: {json.dumps(item)}")
    return "\n\n".join(out) + "\n\n"


def stream_provider(text: str):
    return make(lambda request: httpx.Response(200, text=text))


# --- 流式补全 ----------------------------------------------------------------------


async def test_stream_skips_comments_junk_and_non_object_chunks():
    text = sse(
        ": keep-alive",  # SSE 注释
        "event: ping",
        "data: ",
        "data: {not json",
        "data: [1, 2]",  # 合法 JSON 但不是对象
        "data: null",
        {"choices": [None, {"delta": None}]},
        {"choices": [{"delta": {"content": "答"}}]},
        {"choices": [{"delta": {"content": 42}}]},  # 非字符串内容不拼进去
        {"choices": [{"delta": {"content": "案"}, "finish_reason": "stop"}]},
        "data: [DONE]",
    )
    raw = await stream_provider(text).complete("m", MESSAGES, {"stream": True})
    assert raw.text == "答案" and raw.finish_reason == "stop"
    assert raw.input_tokens == 0 and raw.reported_cost_usd is None  # 没有 usage 片段


async def test_stream_length_finish_is_truncated_and_keeps_last_reason():
    text = sse(
        {"choices": [{"delta": {"content": "a"}, "finish_reason": "length"}]},
        {"choices": [{"delta": {"content": "b"}}]},  # 后来的片段没写 finish_reason：保留上一个
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 9,
                "cost": "0.002",
                "prompt_tokens_details": {"cached_tokens": 3},
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        },
    )
    raw = await stream_provider(text).complete("m", MESSAGES, {"stream": True})
    assert raw.truncated and raw.finish_reason == "length"
    assert (raw.input_tokens, raw.output_tokens, raw.cached_tokens) == (7, 9, 3)
    assert raw.reasoning_tokens == 5 and raw.reported_cost_usd == pytest.approx(0.002)


async def test_stream_usage_only_is_accepted_as_empty_answer():
    text = sse({"choices": [], "usage": {"prompt_tokens": 4, "completion_tokens": 0}})
    raw = await stream_provider(text).complete("m", MESSAGES, {"stream": True})
    assert raw.text == "" and raw.input_tokens == 4


async def test_stream_content_filter_is_refusal():
    text = sse({"choices": [{"delta": {"content": "x"}, "finish_reason": "content_filter"}]})
    with pytest.raises(ProviderError) as err:
        await stream_provider(text).complete("m", MESSAGES, {"stream": True})
    assert err.value.kind == ErrorKind.REFUSAL


async def test_stream_keeps_caller_stream_options():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=sse({"choices": [{"delta": {"content": "x"}}]}))

    await make(handler).complete("m", MESSAGES, {"stream": True, "stream_options": {"x": 1}})
    assert seen["body"]["stream_options"] == {"x": 1}


async def test_stream_error_chunk_without_code_is_server_error_and_redacted():
    text = sse({"error": {"message": f"boom {KEY}"}})
    with pytest.raises(ProviderError) as err:
        await stream_provider(text).complete("m", MESSAGES, {"stream": True})
    assert err.value.kind == ErrorKind.SERVER
    assert KEY not in str(err.value) and KEY not in err.value.detail


async def test_stream_http_error_is_classified_before_parsing():
    provider = make(
        lambda request: httpx.Response(
            429, json={"error": {"message": "slow"}}, headers={"retry-after": "7"}
        )
    )
    with pytest.raises(ProviderError) as err:
        await provider.complete("m", MESSAGES, {"stream": True})
    assert err.value.kind == ErrorKind.RATE_LIMIT
    assert err.value.status == 429 and err.value.retry_after == 7.0


@pytest.mark.parametrize("code", ["insufficient_quota", "billing_error", "insufficient_balance"])
async def test_stream_quota_error_chunk_is_quota(code):
    # 回归：错误块的 code 是文字时曾一律当 500（对方出错），路由会冷却后重试而不是停用渠道
    text = sse({"error": {"code": code, "message": "no credits"}})
    with pytest.raises(ProviderError) as err:
        await stream_provider(text).complete("m", MESSAGES, {"stream": True})
    assert err.value.kind == ErrorKind.QUOTA


@pytest.mark.parametrize(
    ("chunk", "kind"),
    [
        ({"error": {"type": "insufficient_quota", "message": "x"}}, ErrorKind.QUOTA),
        ({"error": {"code": "402", "message": "x"}}, ErrorKind.QUOTA),
        ({"error": {"code": "401", "message": "x"}}, ErrorKind.AUTH),
        ({"error": {"code": "rate_limit_exceeded", "message": "x"}}, ErrorKind.SERVER),
    ],
)
async def test_music_stream_error_chunk_is_classified_like_completion(chunk, kind):
    provider = make(lambda r: httpx.Response(200, text=sse(chunk)))
    with pytest.raises(ProviderError) as err:
        await provider.generate_music("song", "一首歌", {})
    assert err.value.kind == kind


# --- 非流式的小边界 ----------------------------------------------------------------


async def test_non_string_content_is_stringified_and_null_content_is_empty():
    def handler(request):
        content = json.loads(request.content)["messages"][0]["content"]
        reply = None if content == "null" else ["part"]
        return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})

    raw = await make(handler).complete("m", [Message("user", "list")], {})
    assert raw.text == "['part']"
    raw = await make(handler).complete("m", [Message("user", "null")], {})
    assert raw.text == "" and raw.input_tokens == 0


async def test_http_error_with_plain_text_body_is_redacted():
    provider = make(lambda request: httpx.Response(500, text=f"upstream died {KEY}"))
    with pytest.raises(ProviderError) as err:
        await provider.complete("m", MESSAGES, {})
    assert err.value.kind == ErrorKind.SERVER and err.value.status == 500
    assert "upstream died" in err.value.detail and KEY not in err.value.detail


async def test_video_attachment_is_sent_as_video_url():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}]})

    clip = Media("video", "video/mp4", b"mp4bytes")
    await make(handler).complete("m", [Message("user", "看", media=(clip,))], {})
    parts = seen["body"]["messages"][0]["content"]
    assert parts[1]["type"] == "video_url"
    assert parts[1]["video_url"]["url"].startswith("data:video/mp4;base64,")


# --- 图像接口 ----------------------------------------------------------------------


JPEG = b"\xff\xd8\xff\xe0" + b"j" * 20
WEBP = b"RIFF\x00\x00\x00\x00WEBPVP8 " + b"w" * 10
GIF = b"GIF89a" + b"g" * 10


@pytest.mark.parametrize(
    ("data", "mime"),
    [(JPEG, "image/jpeg"), (WEBP, "image/webp"), (GIF, "image/gif"), (FAKE_PNG, "image/png")],
)
def test_sniff_image_mime(data, mime):
    assert _sniff_image_mime(data) == mime


def test_sniff_riff_that_is_not_webp_falls_back_to_png():
    assert _sniff_image_mime(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "image/png"


async def test_images_api_skips_bad_items_and_fetches_http_urls():
    b64 = base64.b64encode(JPEG).decode()
    calls = []

    def handler(request: httpx.Request):
        calls.append(str(request.url))
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "data": [
                        "not a dict",
                        {"b64_json": "!!!not base64!!!"},
                        {"b64_json": b64, "media_type": "application/octet-stream"},
                        {"url": "data:image/gif;base64," + base64.b64encode(GIF).decode()},
                        {"url": "data:garbage"},
                        {"url": "https://cdn.example.test/ok.webp"},
                        {"url": "https://cdn.example.test/missing.png"},
                        {"url": "https://cdn.example.test/empty.png"},
                        {"url": "ftp://nope"},
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4, "cost": 0.04},
                },
            )
        if request.url.path.endswith("ok.webp"):
            return httpx.Response(200, content=WEBP)
        if request.url.path.endswith("empty.png"):
            return httpx.Response(200, content=b"")
        return httpx.Response(404)

    out = await make(handler).generate_image("img", "猫", {"max_tokens": 9}, api="images")
    mimes = [m.mime for m in out.images]
    # 坏 base64 跳过；声明的类型不是图片时按内容判断；坏的 data URI、404、空内容都跳过
    assert mimes == ["image/jpeg", "image/gif", "image/webp"]
    assert out.cost_usd == pytest.approx(0.04)
    assert (out.input_tokens, out.output_tokens) == (3, 4)
    assert sum("cdn.example.test" in c for c in calls) == 3


async def test_images_api_http_url_network_error_is_skipped():
    def handler(request: httpx.Request):
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"url": "https://cdn.example.test/down.png"},
                        {"b64_json": base64.b64encode(FAKE_PNG).decode()},
                    ]
                },
            )
        raise httpx.ConnectError("down")

    out = await make(handler).generate_image("img", "猫", {}, api="images")
    assert len(out.images) == 1 and out.cost_usd is None


async def test_images_api_non_json_body_is_invalid_response():
    provider = make(lambda request: httpx.Response(200, text="<html>"))
    with pytest.raises(ProviderError) as err:
        await provider.generate_image("img", "猫", {}, api="images")
    assert err.value.kind == ErrorKind.INVALID_RESPONSE


async def test_images_api_timeout_and_network_errors():
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    def network(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(ProviderError) as err:
        await make(timeout).generate_image("img", "猫", {}, api="images")
    assert err.value.kind == ErrorKind.TIMEOUT
    with pytest.raises(ProviderError) as err:
        await make(network).generate_image("img", "猫", {}, api="images")
    assert err.value.kind == ErrorKind.NETWORK and "ConnectError" in err.value.detail


# --- 语音、音乐、视频 ---------------------------------------------------------------


async def test_speech_empty_body_is_invalid_and_unknown_format_defaults_to_mpeg():
    with pytest.raises(ProviderError) as err:
        await make(lambda r: httpx.Response(200, content=b"")).synthesize_speech("t", "你好", {})
    assert err.value.kind == ErrorKind.INVALID_RESPONSE

    out = await make(lambda r: httpx.Response(200, content=b"aac")).synthesize_speech(
        "t", "你好", {"response_format": "aac"}
    )
    assert out.media.mime == "audio/mpeg" and out.media.name == "speech.aac"


async def test_music_skips_bad_base64_pieces_and_keeps_good_ones():
    good = base64.b64encode(b"ID3audio").decode()
    text = sse(
        "data: {oops",
        {"choices": [{"delta": {"audio": {"data": "@@@"}}}]},
        {"choices": [{"delta": {"audio": {"data": good}, "content": "歌词"}}]},
        {"choices": [], "usage": {"cost": 0.1}},
    )
    out = await make(lambda r: httpx.Response(200, text=text)).generate_music(
        "song", "一首歌", {"format": "wav", "max_tokens": 5}
    )
    assert out.media.data == b"ID3audio" and out.media.mime == "audio/wav"
    assert out.cost_usd == pytest.approx(0.1) and out.text == "歌词"


async def test_fetch_video_empty_and_non_video_content_type():
    job = VideoJob("j1", "completed")
    with pytest.raises(ProviderError) as err:
        await make(lambda r: httpx.Response(200, content=b"")).fetch_video("v", job)
    assert err.value.kind == ErrorKind.INVALID_RESPONSE

    media = await make(
        lambda r: httpx.Response(
            200, content=b"data", headers={"content-type": "application/octet-stream"}
        )
    ).fetch_video("v", job, index=2)
    assert media.mime == "video/mp4"

    media = await make(
        lambda r: httpx.Response(
            200, content=b"data", headers={"content-type": "video/webm; codecs=vp9"}
        )
    ).fetch_video("v", job)
    assert media.mime == "video/webm"


async def test_video_job_without_id_is_invalid():
    provider = make(lambda r: httpx.Response(200, json={"status": "queued"}))
    with pytest.raises(ProviderError) as err:
        await provider.submit_video("v", "海", {})
    assert err.value.kind == ErrorKind.INVALID_RESPONSE


async def test_poll_keeps_previous_id_and_polling_url_and_redacts_error():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"status": "failed", "error": f"bad {KEY}"})

    job = VideoJob("j9", "running", polling_url="videos/j9")
    done = await make(handler).poll_video("v", job)
    assert seen == ["/v1/videos/j9"]
    assert (done.job_id, done.state, done.polling_url) == ("j9", "failed", "videos/j9")
    assert done.error and KEY not in done.error


async def test_poll_without_polling_url_uses_job_id():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        return httpx.Response(200, json={"id": "j2", "status": "queued"})

    done = await make(handler).poll_video("v", VideoJob("j2"))
    assert seen == ["/v1/videos/j2"] and done.state == "pending"


async def test_absolute_polling_url_under_base_is_made_relative():
    url = "https://api.example.test/v1/videos/j3/status"
    provider = make(lambda r: httpx.Response(200, json={"id": "j3", "polling_url": url}))
    job = await provider.submit_video("v", "海", {})
    assert job.polling_url == "videos/j3/status"


async def test_foreign_polling_url_is_kept_as_is():
    url = "https://elsewhere.example.test/jobs/j4"
    provider = make(lambda r: httpx.Response(200, json={"id": "j4", "polling_url": url}))
    job = await provider.submit_video("v", "海", {})
    assert job.polling_url == url


async def test_single_image_is_only_first_frame():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "j5"})

    img = Media("image", "image/png", FAKE_PNG)
    await make(handler).submit_video("v", "海", {}, images=[img])
    frames = seen["body"]["frame_images"]
    assert [f["frame_type"] for f in frames] == ["first_frame"]


# --- 余额 --------------------------------------------------------------------------


async def test_balance_with_non_dict_data_on_both_endpoints_is_invalid():
    provider = make(lambda r: httpx.Response(200, json={"data": ["x"]}), balance="credits")
    with pytest.raises(ProviderError) as err:
        await provider.account_balance()
    assert err.value.kind == ErrorKind.INVALID_RESPONSE


async def test_balance_ignores_non_numeric_amounts():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/credits"):
            return httpx.Response(200, json={"data": {"total_credits": True, "total_usage": "3"}})
        return httpx.Response(200, json={"data": "nope"})

    bal = await make(handler, balance="credits").account_balance()
    assert bal.total_credits is None and bal.total_usage is None
    assert bal.notes == ("key：无法解析返回内容",)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1.0), (2.5, 2.5), (True, None), (False, None), ("3", None), (None, None)],
)
def test_money_only_accepts_real_numbers(value, expected):
    assert _money(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [("0.5", 0.5), (2, 2.0), (None, None), ("x", None), ([1], None)]
)
def test_float_or_none(value, expected):
    assert _float_or_none(value) == expected
