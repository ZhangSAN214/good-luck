"""Provider 基类、注册表、各渠道适配器、渠道路由与计费。"""

# 导入适配器模块以完成注册
from . import anthropic_adapter, fake, gemini, openai_compat  # noqa: F401
from .base import (
    VIDEO_FINAL,
    Completion,
    ImageOutput,
    Media,
    MediaOutput,
    Message,
    Provider,
    RawCompletion,
    Transcription,
    VideoJob,
)
from .cost import estimate_cost
from .errors import (
    AllChannelsFailed,
    Attempt,
    ErrorKind,
    NoChannelAvailable,
    ProviderError,
    UnsupportedCapability,
)
from .fake import FakeProvider
from .registry import adapter_names, build_providers, register_adapter
from .router import ChannelRouter, Invocation, RoutePlan
from .secrets import KeyRing, Secret, redact

__all__ = [
    "AllChannelsFailed",
    "Attempt",
    "ChannelRouter",
    "Completion",
    "ErrorKind",
    "FakeProvider",
    "Invocation",
    "KeyRing",
    "VIDEO_FINAL",
    "ImageOutput",
    "Media",
    "MediaOutput",
    "Transcription",
    "VideoJob",
    "Message",
    "NoChannelAvailable",
    "Provider",
    "ProviderError",
    "RawCompletion",
    "RoutePlan",
    "Secret",
    "UnsupportedCapability",
    "adapter_names",
    "build_providers",
    "estimate_cost",
    "redact",
    "register_adapter",
]
