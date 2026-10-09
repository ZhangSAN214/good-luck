"""测试与离线开发用的 Fake provider：按脚本返回结果或抛出指定错误，不联网。"""

from __future__ import annotations

import base64
import io
import wave
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from roundtable.core.config.schema import ChannelSpec

from .base import (
    ImageOutput,
    Media,
    MediaOutput,
    Message,
    Provider,
    RawCompletion,
    Transcription,
    VideoJob,
)
from .errors import ErrorKind, ProviderError
from .registry import register_adapter
from .secrets import Secret

# 一个脚本项：文本（成功）、ErrorKind（失败）、RawCompletion，或根据请求动态生成的函数
Outcome = str | ErrorKind | RawCompletion | Callable[[str, Sequence[Message]], "Outcome"]


# 1×1 像素的 PNG；最小的 WAV（0.1 秒静音）；不可解码的"视频"字节
FAKE_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    return buf.getvalue()


FAKE_WAV = _wav()
FAKE_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"fake-video"


@dataclass(frozen=True)
class FakeCall:
    model: str
    messages: tuple[Message, ...]
    params: dict[str, Any]


class FakeProvider(Provider):
    """
    script: 模型 → 依次返回的结果；用完后返回 default。
    default: 未写脚本时的结果；默认回显最后一条消息。
    """

    def __init__(
        self,
        channel: str = "fake",
        script: dict[str, Iterable[Outcome]] | None = None,
        default: Outcome | None = None,
        reported_cost_usd: float | None = None,
    ) -> None:
        super().__init__(channel)
        self._script: dict[str, deque[Outcome]] = defaultdict(deque)
        for model, outcomes in (script or {}).items():
            self._script[model].extend(outcomes)
        self._default = default
        self._reported_cost = reported_cost_usd
        self.calls: list[FakeCall] = []
        # 媒体：每次调用记录为 (种类, 模型, 输入, 参数)；cost 为 None 时由路由按配置价格估算
        self.media_calls: list[tuple[str, str, Any, dict[str, Any]]] = []
        self.media_cost: float | None = None
        self.broken_models: set[str] = set()  # 画图时总是失败的模型（测试换模型重试用）
        self.transcript = "说话人 1：这是一段测试转写。"
        # 视频任务：poll 依次返回的状态（用完后一直 completed）；ErrorKind 表示该次轮询抛错
        self.video_states: deque[str | ErrorKind] = deque()
        self.submit_errors: deque[ErrorKind] = deque()
        self.video_error = "生成失败"
        self._jobs = 0

    def queue(self, model: str, *outcomes: Outcome) -> None:
        self._script[model].extend(outcomes)

    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        self.calls.append(FakeCall(model, tuple(messages), dict(params)))
        if self._script[model]:
            outcome = self._script[model].popleft()
        elif self._default is not None:
            outcome = self._default
        else:
            outcome = f"[{self.channel}:{model}] {messages[-1].content if messages else ''}"
        return self._resolve(outcome, model, messages)

    # --- 媒体 ------------------------------------------------------------------

    def _media_fail(self, errors: deque[ErrorKind]) -> None:
        if errors:
            raise ProviderError(errors.popleft(), self.channel, "fake")

    async def generate_image(
        self, model: str, prompt: str, params: dict[str, Any], images=(), api: str = "chat"
    ) -> ImageOutput:
        self.media_calls.append(("image", model, prompt, dict(params), tuple(images), api))
        if model in self.broken_models:
            raise ProviderError(ErrorKind.NOT_FOUND, self.channel, "fake: 模型不能用这个接口")
        self._media_fail(self.submit_errors)
        return ImageOutput((Media("image", "image/png", FAKE_PNG),), self.media_cost)

    async def synthesize_speech(self, model: str, text: str, params: dict[str, Any]) -> MediaOutput:
        self.media_calls.append(("speech", model, text, dict(params)))
        self._media_fail(self.submit_errors)
        return MediaOutput(Media("audio", "audio/wav", FAKE_WAV, "speech.wav"), self.media_cost)

    async def transcribe(self, model: str, audio: Media, params: dict[str, Any]) -> Transcription:
        self.media_calls.append(("transcribe", model, audio.describe(), dict(params)))
        self._media_fail(self.submit_errors)
        return Transcription(self.transcript, self.media_cost, 6.0)

    async def submit_video(
        self, model: str, prompt: str, params: dict[str, Any], images=()
    ) -> VideoJob:
        self.media_calls.append(("video", model, prompt, dict(params)))
        self._media_fail(self.submit_errors)
        self._jobs += 1
        return VideoJob(f"job-{self._jobs}", "pending", f"videos/job-{self._jobs}")

    async def poll_video(self, model: str, job: VideoJob) -> VideoJob:
        self.media_calls.append(("poll", model, job.job_id, {}))
        state = self.video_states.popleft() if self.video_states else "completed"
        if isinstance(state, ErrorKind):
            raise ProviderError(state, self.channel, "fake")
        if state == "completed":
            return VideoJob(
                job.job_id,
                "completed",
                job.polling_url,
                (f"videos/{job.job_id}/content?index=0",),
                self.media_cost,
            )
        error = self.video_error if state in ("failed", "cancelled", "expired") else None
        return VideoJob(job.job_id, state, job.polling_url, (), None, error)  # type: ignore[arg-type]

    async def fetch_video(self, model: str, job: VideoJob, index: int = 0) -> Media:
        self.media_calls.append(("fetch", model, job.job_id, {}))
        return Media("video", "video/mp4", FAKE_MP4, "video.mp4")

    def _resolve(self, outcome: Outcome, model: str, messages: Sequence[Message]) -> RawCompletion:
        if callable(outcome) and not isinstance(outcome, ErrorKind | RawCompletion):
            outcome = outcome(model, messages)
        if isinstance(outcome, ErrorKind):
            raise ProviderError(outcome, self.channel, "fake")
        if isinstance(outcome, RawCompletion):
            return outcome
        text = str(outcome)
        prompt_chars = sum(len(m.content) for m in messages)
        return RawCompletion(
            text=text,
            input_tokens=max(1, prompt_chars // 4),
            output_tokens=max(1, len(text) // 4),
            reported_cost_usd=self._reported_cost,
        )


@register_adapter("fake")
def _fake_factory(
    channel: str, spec: ChannelSpec, key: Secret | None, timeout_s: float
) -> FakeProvider:
    """允许在配置中声明 adapter: fake，用于离线演示。"""
    return FakeProvider(channel)
