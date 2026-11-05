"""服务层共用的错误类型、常量和小工具（从 service.py 拆出，代码精简）。"""

from __future__ import annotations

import logging

log = logging.getLogger("roundtable.core.service")  # 日志名沿用拆分前的


# 讨论停下来（结束或等待用户）的状态：此时推送流会关闭
RESTING = frozenset({"completed", "stopped", "failed", "awaiting_confirmation", "paused"})


REVEALABLE = frozenset({"completed", "stopped", "failed"})


MAX_QUESTION_CHARS = 20_000


MAX_CHAT_CHARS = 2000


HANDOFF_FILES = 5  # 交办时最多带几个你在聊天里发的文件


MAX_MOMENT_CHARS = 500


ADB_QR_TTL_S = 180  # 配对二维码几秒内有效（手机上的扫码页面本身也会超时）


# 公寓楼：进行中的会话这么久没有更新就不算在上班（服务意外退出后留下的 running）
WORKING_WINDOW_H = 3


EXPORT_FOLDER_KEY = "export.folder"  # 导出时「存到固定文件夹」记住的文件夹


def _has_tk() -> bool:
    """这台电脑的 Python 带不带 tkinter（一起看的悬浮窗要用）。"""
    import importlib.util

    return importlib.util.find_spec("tkinter") is not None


class ServiceError(ValueError):
    """请求不合法（参数错误、状态不允许等）。status 为建议的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


# 转发时带过去的附加信息（图片、卡片、表情包说明文字）
FORWARD_KEYS = ("media", "caption", "auto_text", "card", "perception")
