"""Provider 抽象：每个渠道一个实例。v1 只实现文本补全，媒体能力的方法签名已预留。"""

from __future__ import annotations

import base64
import binascii
import hashlib
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import Attempt, UnsupportedCapability

Role = Literal["system", "user", "assistant"]


MediaKind = Literal["image", "audio"]


@dataclass(frozen=True)
class Media:
    """随消息发送的图片或音频（附件）。data 不出现在 repr 中，避免进入日志。"""

    kind: MediaKind
    mime: str
    data: bytes = field(repr=False)
    name: str = ""

    def describe(self) -> dict[str, Any]:
        """存库与日志用的描述（不含内容本身）。"""
        return {
            "kind": self.kind,
            "mime": self.mime,
            "bytes": len(self.data),
            "sha256": hashlib.sha256(self.data).hexdigest(),
        }


def media_from_data_uri(url: str) -> Media | None:
    """解析 data:image/png;base64,... 形式的图片；格式不符时返回 None。"""
    head, sep, body = url.partition(",")
    if not sep or not head.startswith("data:image/") or not head.endswith(";base64"):
        return None
    try:
        data = base64.b64decode(body, validate=True)
    except (ValueError, binascii.Error):
        return None
    return Media("image", head[5:-7], data)


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    # 附件中的图片 / 音频，放在文字之后（只用于 user 消息）
    media: tuple[Media, ...] = ()


@dataclass(frozen=True)
class RawCompletion:
    """适配器返回的原始结果（尚未附加渠道与费用信息）。

    input_tokens 含缓存命中部分；cached_tokens 是其中命中缓存的数量。
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reported_cost_usd: float | None = None
    truncated: bool = False  # 达到输出长度上限被截断（max_tokens）
    images: tuple[Media, ...] = ()  # 图像生成模型返回的图片


@dataclass(frozen=True)
class ImageOutput:
    """图像生成的原始结果。cost_usd 为渠道返回的实际费用（没有时为 None，由路由按配置估算）。"""

    images: tuple[Media, ...]
    cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class MediaOutput:
    """语音合成等一次性产出的音频 / 视频。"""

    media: Media
    cost_usd: float | None = None


@dataclass(frozen=True)
class Transcription:
    text: str
    cost_usd: float | None = None
    seconds: float | None = None  # 渠道计费的音频时长


VideoState = Literal["pending", "running", "completed", "failed", "cancelled", "expired"]
VIDEO_FINAL: frozenset[str] = frozenset({"completed", "failed", "cancelled", "expired"})


@dataclass(frozen=True)
class VideoJob:
    """异步视频任务。job_id / polling_url 存库，暂停恢复后继续轮询同一个任务。"""

    job_id: str
    state: VideoState = "pending"
    polling_url: str | None = None
    content_urls: tuple[str, ...] = ()
    cost_usd: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class Completion:
    """一次成功调用的完整记录。channel 是实际走的渠道。"""

    text: str
    model_id: str
    channel: str
    channel_kind: str
    route_model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    cost_usd: float
    cost_source: Literal["reported", "estimated"]
    latency_s: float
    attempts: tuple[Attempt, ...] = field(default_factory=tuple)
    truncated: bool = False  # 输出达到长度上限被截断
    images: tuple[Media, ...] = ()  # 图像生成模型返回的图片

    @property
    def failed_over(self) -> bool:
        return len(self.attempts) > 1


class Provider(ABC):
    """一个渠道的适配器。不做重试 —— 重试与切换由 ChannelRouter 负责。"""

    def __init__(self, channel: str) -> None:
        self.channel = channel

    @abstractmethod
    async def complete(
        self, model: str, messages: Sequence[Message], params: dict[str, Any]
    ) -> RawCompletion:
        """文本补全。失败时只抛 ProviderError。"""

    # --- 媒体能力：默认不支持（适配器按需实现）-------------------------------------

    async def generate_image(
        self,
        model: str,
        prompt: str,
        params: dict[str, Any],
        images: Sequence[Media] = (),
        api: str = "chat",
    ) -> ImageOutput:
        """图像生成。默认走对话接口（模型在回复中返回图片，如 OpenRouter 的 modalities）；
        api="images" 走专门的图像接口（适配器按需实现，默认不支持）。"""
        if api == "images":
            raise UnsupportedCapability(f"{self.channel} 不支持图像接口")
        raw = await self.complete(model, [Message("user", prompt, tuple(images))], params)
        return ImageOutput(
            raw.images,
            raw.reported_cost_usd,
            raw.input_tokens,
            raw.output_tokens,
        )

    async def synthesize_speech(self, model: str, text: str, params: dict[str, Any]) -> MediaOutput:
        raise UnsupportedCapability(f"{self.channel} 不支持语音合成")

    async def transcribe(self, model: str, audio: Media, params: dict[str, Any]) -> Transcription:
        raise UnsupportedCapability(f"{self.channel} 不支持语音转写")

    async def submit_video(
        self, model: str, prompt: str, params: dict[str, Any], images: Sequence[Media] = ()
    ) -> VideoJob:
        raise UnsupportedCapability(f"{self.channel} 不支持视频生成")

    async def poll_video(self, model: str, job: VideoJob) -> VideoJob:
        raise UnsupportedCapability(f"{self.channel} 不支持视频生成")

    async def fetch_video(self, model: str, job: VideoJob, index: int = 0) -> Media:
        raise UnsupportedCapability(f"{self.channel} 不支持视频生成")

    async def aclose(self) -> None:  # noqa: B027 - 可选覆盖
        """释放连接。"""
