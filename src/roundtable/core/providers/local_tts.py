"""本地配音服务（如自己电脑上的 IndexTTS2 网页服务，默认 http://127.0.0.1:7860）：只做语音合成，
不花钱，不需要 key。

不同的本地服务接口各不一样，所以请求和取音频的方式都写在模型 / 路由的 params 里（装好后在
config/models.local.yaml 按实际接口改），代码里不写死任何一家的接口：

    path          合成接口的路径（相对 base_url），默认 "tts"
    request       "json"（默认）或 "form"（表单，和网页上的表单提交一样）
    fields        请求字段：{字段名: 值}；值里的 {{text}} / {{voice}} 换成要念的文字和音色
                  （音色 = 服务里存好的参考音频预设名）；其余值原样发
                  默认 {"text": "{{text}}", "voice": "{{voice}}"}
    audio_field   回 JSON 时，音频在哪个字段（base64，可以写成 "data.audio" 这样的路径）
    url_field     回 JSON 时，音频文件的地址在哪个字段（相对 base_url 或完整地址，取回来）
    timeout_s     最多等多久（长句、首次加载模型较慢），默认 300

返回值三种都认：
- 直接回音频（content-type 是 audio/*，或开头是 RIFF / ID3 / OggS / fLaC）；
- 回 JSON：按 audio_field / url_field 取；
- 回 SSE 进度流（text/event-stream）：取最后一个带 JSON 的事件，再按上一条取。

规则同其他适配器：不重试（失败抛 ProviderError，换模型由调用方负责）；外部错误文本脱敏截断、
用 from None 切断异常链。服务没开时抛 NETWORK，成员语音会换下一个语音模型。
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Sequence
from typing import Any

import httpx

from roundtable.core.config.schema import ChannelSpec

from ._http import kind_for_status, safe_detail
from .base import Media, MediaOutput, Message, Provider, RawCompletion
from .errors import ErrorKind, ProviderError, UnsupportedCapability
from .registry import register_adapter
from .secrets import Secret

DEFAULTS: dict[str, Any] = {
    "path": "tts",
    "request": "json",
    "fields": {"text": "{{text}}", "voice": "{{voice}}"},
    "audio_field": None,
    "url_field": None,
    "timeout_s": 300.0,
}
_MIME_BY_EXT = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac"}


def sniff_audio(data: bytes) -> str | None:
    """按文件头认音频类型；认不出来返回 None。"""
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[:4] == b"fLaC":
        return "audio/flac"
    return None


def fill_fields(fields: dict[str, Any], text: str, voice: str) -> dict[str, Any]:
    """把字段值里的 {{text}} / {{voice}} 换掉；非字符串原样保留。"""
    out: dict[str, Any] = {}
    for name, value in fields.items():
        if isinstance(value, str):
            value = value.replace("{{text}}", text).replace("{{voice}}", voice)
        out[name] = value
    return out


def dig(body: Any, path: str) -> Any:
    """按 "a.b.0.c" 取嵌套字段；取不到返回 None。"""
    for part in path.split("."):
        if isinstance(body, dict):
            body = body.get(part)
        elif isinstance(body, list) and part.isdigit() and int(part) < len(body):
            body = body[int(part)]
        else:
            return None
    return body


def last_sse_json(text: str) -> Any:
    """SSE 流里最后一个能解析成 JSON 的 data。"""
    found = None
    for block in text.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line[5:].lstrip() for line in block.split("\n") if line.startswith("data:")
        )
        if not data:
            continue
        try:
            found = json.loads(data)
        except ValueError:
            continue
    return found


@register_adapter("local_tts")
class LocalTTSProvider(Provider):
    def __init__(
        self,
        channel: str,
        spec: ChannelSpec,
        key: Secret | None,  # 不用：本地服务不需要 key
        timeout_s: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(channel)
        self._client = httpx.AsyncClient(
            base_url=spec.base_url.rstrip("/") + "/", timeout=timeout_s, transport=transport
        )

    def _error(self, kind: ErrorKind, detail: str = "", **kw: Any) -> ProviderError:
        return ProviderError(kind, self.channel, safe_detail(detail), **kw)

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        raise UnsupportedCapability(f"{self.channel} 只能配音")

    async def _send(self, method: str, path: str, timeout: float, **kw: Any) -> httpx.Response:
        try:
            response = await self._client.request(method, path.lstrip("/"), timeout=timeout, **kw)
        except httpx.TimeoutException:
            raise self._error(ErrorKind.TIMEOUT) from None
        except httpx.TransportError as exc:
            # 最常见：本地配音服务没开
            raise self._error(
                ErrorKind.NETWORK, f"连不上本地配音服务（{type(exc).__name__}）"
            ) from None
        if response.status_code >= 400:
            raise self._error(
                kind_for_status(response.status_code),
                response.text,
                status=response.status_code,
            ) from None
        return response

    async def synthesize_speech(self, model: str, text: str, params: dict[str, Any]) -> MediaOutput:
        opts = {**DEFAULTS, **{k: v for k, v in params.items() if k in DEFAULTS}}
        voice = str(params.get("voice") or "")
        fields = opts["fields"] if isinstance(opts["fields"], dict) else DEFAULTS["fields"]
        body = fill_fields(fields, text, voice)
        timeout = float(opts["timeout_s"])
        kw = {"data": body} if opts["request"] == "form" else {"json": body}
        response = await self._send("POST", str(opts["path"]), timeout, **kw)
        media = await self._audio(response, opts, timeout)
        return MediaOutput(media, cost_usd=0.0)

    async def _audio(self, response: httpx.Response, opts: dict[str, Any], timeout: float) -> Media:
        ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
        data = response.content
        if ctype.startswith("audio/") or sniff_audio(data):
            if not data:
                raise self._error(ErrorKind.INVALID_RESPONSE, "返回的音频是空的")
            return Media("audio", sniff_audio(data) or ctype, data)
        if ctype == "text/event-stream":
            body = last_sse_json(response.text)
        else:
            try:
                body = response.json()
            except ValueError:
                raise self._error(
                    ErrorKind.INVALID_RESPONSE, "返回的既不是音频也不是 JSON"
                ) from None
        if opts["audio_field"]:
            raw = dig(body, str(opts["audio_field"]))
            if isinstance(raw, str) and raw:
                try:
                    audio = base64.b64decode(raw.split(",", 1)[-1], validate=True)
                except (binascii.Error, ValueError):
                    raise self._error(ErrorKind.INVALID_RESPONSE, "音频字段不是 base64") from None
                return Media("audio", sniff_audio(audio) or "audio/wav", audio)
        if opts["url_field"]:
            url = dig(body, str(opts["url_field"]))
            if isinstance(url, str) and url:
                fetched = await self._send("GET", url, timeout)
                audio = fetched.content
                ext = url.rsplit(".", 1)[-1].lower()
                mime = sniff_audio(audio) or _MIME_BY_EXT.get(ext)
                if audio and mime:
                    return Media("audio", mime, audio)
        raise self._error(
            ErrorKind.INVALID_RESPONSE, "返回里找不到音频（检查 audio_field / url_field）"
        )

    async def aclose(self) -> None:
        await self._client.aclose()
