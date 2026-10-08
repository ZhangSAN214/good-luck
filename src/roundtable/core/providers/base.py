"""Provider 抽象：每个渠道一个实例。v1 只实现文本补全，媒体能力的方法签名已预留。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import Attempt, UnsupportedCapability

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


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

    # --- 后续扩展的能力：默认不支持 -------------------------------------------

    async def generate_image(self, model: str, prompt: str, params: dict[str, Any]) -> bytes:
        raise UnsupportedCapability(f"{self.channel} 不支持图像生成")

    async def synthesize_speech(self, model: str, text: str, params: dict[str, Any]) -> bytes:
        raise UnsupportedCapability(f"{self.channel} 不支持语音合成")

    async def transcribe(self, model: str, audio: bytes, params: dict[str, Any]) -> str:
        raise UnsupportedCapability(f"{self.channel} 不支持语音转写")

    async def aclose(self) -> None:  # noqa: B027 - 可选覆盖
        """释放连接。"""
