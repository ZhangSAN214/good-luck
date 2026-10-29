"""投屏陪伴（docs/WATCH.md）：用户把电脑屏幕或安卓手机 / 平板的画面给成员看，选几位陪着一起看。

- ``capture.py``：画面从哪来（电脑整屏 / 某个窗口、安卓 adb 截屏）、画面有没有变、敏感窗口判断。
- ``watch.py``：一起看：画面变了才转成文字、成员吐槽和回话、安静模式、每月上限。
"""

from .capture import (
    AdbSource,
    FakeScreenSource,
    Frame,
    PcSource,
    ScreenSource,
    ScreenUnavailable,
    SourceInfo,
    change_ratio,
    encode_frame,
    is_sensitive,
    new_qr_pairing,
    qr_pairing_payload,
)
from .watch import WATCH_PREFIX, ScreenWatch

__all__ = [
    "WATCH_PREFIX",
    "AdbSource",
    "FakeScreenSource",
    "Frame",
    "PcSource",
    "ScreenSource",
    "ScreenUnavailable",
    "ScreenWatch",
    "SourceInfo",
    "change_ratio",
    "encode_frame",
    "is_sensitive",
    "new_qr_pairing",
    "qr_pairing_payload",
]
