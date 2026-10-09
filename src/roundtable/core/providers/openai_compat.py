"""OpenAI 兼容接口（/chat/completions）。用于 OpenRouter、OpenAI、xAI、DeepSeek、本地端点。"""

from __future__ import annotations

import base64
from collections.abc import Sequence
from typing import Any

import httpx

from roundtable.core.config.schema import ChannelSpec

from ._http import error_fields, kind_for_status, retry_after, safe_detail
from .base import (
    ImageOutput,
    Media,
    MediaOutput,
    Message,
    Provider,
    RawCompletion,
    Transcription,
    VideoJob,
    media_from_data_uri,
)
from .errors import ErrorKind, ProviderError
from .registry import register_adapter
from .secrets import Secret

# input_audio 的 format 字段
AUDIO_FORMATS = {"audio/mpeg": "mp3", "audio/wav": "wav", "audio/x-wav": "wav"}


@register_adapter("openai_compat")
class OpenAICompatProvider(Provider):
    def __init__(
        self,
        channel: str,
        spec: ChannelSpec,
        key: Secret | None,
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(channel)
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["Authorization"] = f"Bearer {key.reveal()}"
        self._secrets = [key.reveal()] if key else []
        self._extra_body = dict(spec.extra_body)
        self._aliases = dict(spec.param_aliases)
        self._reasoning = spec.reasoning_style
        self._client = httpx.AsyncClient(
            base_url=spec.base_url, headers=headers, timeout=timeout_s, transport=transport
        )

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.channel, safe_detail(detail, self._secrets), **kw)

    @staticmethod
    def _message(m: Message) -> dict[str, Any]:
        if not m.media:
            return {"role": m.role, "content": m.content}
        parts: list[dict[str, Any]] = [{"type": "text", "text": m.content}]
        for media in m.media:
            data = base64.b64encode(media.data).decode("ascii")
            if media.kind == "image":
                url = f"data:{media.mime};base64,{data}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                fmt = AUDIO_FORMATS.get(media.mime, media.mime.split("/")[-1])
                parts.append({"type": "input_audio", "input_audio": {"data": data, "format": fmt}})
        return {"role": m.role, "content": parts}

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        params = dict(params)
        reasoning = params.pop("reasoning", None)
        if reasoning and self._reasoning == "object":
            params["reasoning"] = reasoning
        elif reasoning and self._reasoning == "effort" and reasoning.get("effort"):
            params["reasoning_effort"] = reasoning["effort"]
        params = {self._aliases.get(k, k): v for k, v in params.items()}
        body = {
            **params,
            **self._extra_body,
            "model": model,
            "messages": [self._message(m) for m in messages],
        }
        try:
            response = await self._client.post("chat/completions", json=body)
        except httpx.TimeoutException:
            raise self._error(ErrorKind.TIMEOUT) from None
        except httpx.TransportError as exc:
            raise self._error(ErrorKind.NETWORK, type(exc).__name__) from None

        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            code, message = error_fields(payload)
            raise self._error(
                kind_for_status(response.status_code, code),
                message or (payload if isinstance(payload, str) else ""),
                status=response.status_code,
                retry_after=retry_after(response.headers),
            ) from None

        try:
            data = response.json()
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
            usage = data.get("usage") or {}
            details = usage.get("prompt_tokens_details") or {}
            out_details = usage.get("completion_tokens_details") or {}
            result = RawCompletion(
                text=text if isinstance(text, str) else str(text),
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                cached_tokens=int(details.get("cached_tokens") or 0),
                reported_cost_usd=_float_or_none(usage.get("cost")),
                truncated=choice.get("finish_reason") == "length",
                images=_images(choice["message"].get("images") or []),
                finish_reason=choice.get("finish_reason"),
                reasoning_tokens=int(out_details.get("reasoning_tokens") or 0),
            )
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析返回内容") from None

        if choice.get("finish_reason") == "content_filter":
            raise self._error(ErrorKind.REFUSAL, "内容被过滤")
        return result

    # --- 媒体：语音合成 / 转写 / 异步视频（OpenRouter 的接口格式）----------------------

    async def _send(self, method: str, url: str, **kw: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, url, **kw)
        except httpx.TimeoutException:
            raise self._error(ErrorKind.TIMEOUT) from None
        except httpx.TransportError as exc:
            raise self._error(ErrorKind.NETWORK, type(exc).__name__) from None
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
            code, message = error_fields(payload)
            raise self._error(
                kind_for_status(response.status_code, code),
                message or (payload if isinstance(payload, str) else ""),
                status=response.status_code,
                retry_after=retry_after(response.headers),
            ) from None
        return response

    def _json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise self._error(ErrorKind.INVALID_RESPONSE, "无法解析返回内容")
        return data

    async def generate_image(
        self,
        model: str,
        prompt: str,
        params: dict[str, Any],
        images: Sequence[Media] = (),
        api: str = "chat",
    ) -> ImageOutput:
        """api="images"：OpenRouter 的 /images 接口（gpt-image 系列不能走对话接口）。参考图作为
        data URI 放进 images；返回 data[*].b64_json 或 url。api="chat" 走对话接口。"""
        if api != "images":
            return await super().generate_image(model, prompt, params, images, api)
        params = {k: v for k, v in params.items() if k not in ("modalities", "max_tokens")}
        body: dict[str, Any] = {"model": model, "prompt": prompt, "n": 1, **params}
        if images:
            body["images"] = [
                {
                    "image_url": f"data:{m.mime};base64,{base64.b64encode(m.data).decode('ascii')}",
                }
                for m in images
            ]
        data = self._json(await self._send("POST", "images", json=body))
        out: list[Media] = []
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            if item.get("b64_json"):
                try:
                    raw = base64.b64decode(item["b64_json"])
                except ValueError:
                    continue
                out.append(Media("image", _sniff_image_mime(raw), raw))
            elif str(item.get("url", "")).startswith("data:"):
                media = media_from_data_uri(item["url"])
                if media is not None:
                    out.append(media)
            elif str(item.get("url", "")).startswith("http"):
                try:
                    got = await self._client.get(item["url"])
                except httpx.HTTPError:
                    continue
                if got.status_code == 200 and got.content:
                    out.append(Media("image", _sniff_image_mime(got.content), got.content))
        if not out:
            raise self._error(ErrorKind.INVALID_RESPONSE, "图像接口没有返回图片")
        usage = data.get("usage") or {}
        return ImageOutput(
            tuple(out),
            _float_or_none(usage.get("cost")),
            int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
            int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
        )

    async def synthesize_speech(self, model: str, text: str, params: dict[str, Any]) -> MediaOutput:
        params = dict(params)
        fmt = params.pop("response_format", "mp3")
        body = {"model": model, "input": text, "response_format": fmt, **params}
        response = await self._send("POST", "audio/speech", json=body)
        if not response.content:
            raise self._error(ErrorKind.INVALID_RESPONSE, "没有返回音频")
        mime = {"mp3": "audio/mpeg", "wav": "audio/wav", "opus": "audio/ogg"}.get(fmt, "audio/mpeg")
        return MediaOutput(Media("audio", mime, response.content, f"speech.{fmt}"))

    async def transcribe(self, model: str, audio: Media, params: dict[str, Any]) -> Transcription:
        fmt = AUDIO_FORMATS.get(audio.mime, audio.mime.split("/")[-1])
        body: dict[str, Any] = {
            "model": model,
            "input_audio": {"data": base64.b64encode(audio.data).decode("ascii"), "format": fmt},
            **params,
        }
        data = self._json(await self._send("POST", "audio/transcriptions", json=body))
        usage = data.get("usage") or {}
        return Transcription(
            text=str(data.get("text") or ""),
            cost_usd=_float_or_none(usage.get("cost")),
            seconds=_float_or_none(usage.get("seconds")),
        )

    async def submit_video(
        self, model: str, prompt: str, params: dict[str, Any], images: Sequence[Media] = ()
    ) -> VideoJob:
        body: dict[str, Any] = {"model": model, "prompt": prompt, **params}
        if images:
            body["frame_images"] = [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{m.mime};base64,{base64.b64encode(m.data).decode('ascii')}"
                    },
                }
                for m in images
            ]
        return self._job(self._json(await self._send("POST", "videos", json=body)))

    async def poll_video(self, model: str, job: VideoJob) -> VideoJob:
        url = job.polling_url or f"videos/{job.job_id}"
        return self._job(self._json(await self._send("GET", url)), job)

    async def fetch_video(self, model: str, job: VideoJob, index: int = 0) -> Media:
        response = await self._send("GET", f"videos/{job.job_id}/content", params={"index": index})
        if not response.content:
            raise self._error(ErrorKind.INVALID_RESPONSE, "没有返回视频")
        mime = response.headers.get("content-type", "video/mp4").split(";")[0].strip()
        return Media("video", mime if mime.startswith("video/") else "video/mp4", response.content)

    def _job(self, data: dict[str, Any], previous: VideoJob | None = None) -> VideoJob:
        job_id = str(data.get("id") or (previous.job_id if previous else ""))
        if not job_id:
            raise self._error(ErrorKind.INVALID_RESPONSE, "没有返回任务 id")
        status = str(data.get("status") or "pending")
        state = {"in_progress": "running", "processing": "running", "queued": "pending"}.get(
            status, status
        )
        if state not in ("pending", "running", "completed", "failed", "cancelled", "expired"):
            state = "running"
        polling = data.get("polling_url") or (previous.polling_url if previous else None)
        if isinstance(polling, str) and polling.startswith(str(self._client.base_url).rstrip("/")):
            polling = polling[len(str(self._client.base_url)) :].lstrip("/")
        elif isinstance(polling, str) and polling.startswith("/api/v1/"):
            polling = polling.removeprefix("/api/v1/")
        usage = data.get("usage") or {}
        error = data.get("error")
        return VideoJob(
            job_id=job_id,
            state=state,
            polling_url=polling if isinstance(polling, str) else None,
            content_urls=tuple(str(u) for u in (data.get("unsigned_urls") or ())),
            cost_usd=_float_or_none(usage.get("cost")),
            error=safe_detail(str(error), self._secrets) if error else None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def _sniff_image_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"GIF8"):
        return "image/gif"
    return "image/png"


def _images(items: list[Any]) -> tuple[Media, ...]:
    """图像输出（OpenRouter 等）：message.images[*].image_url.url 为 data URI。"""
    out = []
    for item in items:
        url = ((item or {}).get("image_url") or {}).get("url") or ""
        media = media_from_data_uri(url)
        if media is not None:
            out.append(media)
    return tuple(out)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
