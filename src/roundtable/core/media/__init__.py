"""媒体生成：模型选择、计价、图片 / 语音 / 视频生成服务、视频截帧。

MediaService 依赖存储层，而存储层又依赖路由；为避免循环导入，服务相关的名称按需加载。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .frames import FramesUnavailable, extract_frames
from .pricing import KIND_LABELS, KIND_TAGS, KINDS, estimate_generation, unit_cost
from .select import media_group, pick_media_model, pick_stt_model

if TYPE_CHECKING:
    from .service import MediaResult, MediaService, Placement

__all__ = [
    "KINDS",
    "KIND_LABELS",
    "KIND_TAGS",
    "FramesUnavailable",
    "MediaResult",
    "MediaService",
    "Placement",
    "estimate_generation",
    "extract_frames",
    "media_group",
    "pick_media_model",
    "pick_stt_model",
    "unit_cost",
]


def __getattr__(name: str) -> Any:
    if name in ("MediaResult", "MediaService", "Placement"):
        from . import service

        return getattr(service, name)
    raise AttributeError(name)
