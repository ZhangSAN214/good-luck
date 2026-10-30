"""「关于你」的电脑状态（成员系统 P8 第二块）：电脑多久没人动了、前台是不是在玩游戏。

- **默认不读**：只有你在「关于你 → 你的状态」里打开对应开关，成员聊天时才会问一次；读到的东西
  不写数据库、不进日志，只换成一句话（「用户的电脑十几分钟没人动了」「用户在玩：原神」）。
- **空闲**：Windows 的 ``GetLastInputInfo``（最后一次按键盘 / 动鼠标是多久以前），不看你在做什么。
- **游戏**：只看前台窗口的程序文件路径，和游戏表（配置里的 + 你自己加的）比；Steam / Epic 装的
  游戏按 ``steamapps\\common\\<游戏文件夹>`` 认。不是游戏就什么都不报（别的程序叫什么一律不说）。
- 不在 Windows 上时返回不可用和原因，绝不抛错。测试中注入假的来源（``Runtime.pc_source``）。
"""

from __future__ import annotations

import contextlib
import re
import sys
from dataclasses import dataclass
from typing import Protocol

STORE_DIRS = re.compile(r"[\\/](?:steamapps[\\/]common|epic games)[\\/]([^\\/]+)[\\/]", re.I)


@dataclass(frozen=True)
class PcState:
    idle_seconds: float | None = None  # None：读不到
    foreground: str = ""  # 前台程序的完整路径（只在内存里用来认游戏）


class PcSource(Protocol):
    def available(self) -> str | None:
        """不可用时返回原因（给用户看的中文）。"""

    def read(self) -> PcState: ...


def game_of(path: str, games: dict[str, str]) -> str | None:
    """前台程序是不是游戏：按程序文件名（不分大小写）查表，再看是不是 Steam / Epic 装的。"""
    if not path:
        return None
    exe = re.split(r"[\\/]", path)[-1].lower()
    for name, label in games.items():
        if name.lower() == exe:
            return label
    m = STORE_DIRS.search(path)
    return m.group(1) if m else None


class WindowsPc:
    """真正读 Windows（ctypes，不装额外的包）。"""

    def available(self) -> str | None:
        return None if sys.platform == "win32" else "只在 Windows 电脑上能读"

    def read(self) -> PcState:
        if sys.platform != "win32":
            return PcState()
        import ctypes
        from ctypes import wintypes

        class LastInput(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        idle = None
        info = LastInput()
        info.cbSize = ctypes.sizeof(LastInput)
        if user32.GetLastInputInfo(ctypes.byref(info)):
            idle = ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0
        path = ""
        with contextlib.suppress(OSError):
            hwnd = user32.GetForegroundWindow()
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            handle = kernel32.OpenProcess(0x1000, False, pid.value)  # 只查询有限信息
            if handle:
                try:
                    buf = ctypes.create_unicode_buffer(1024)
                    size = wintypes.DWORD(1024)
                    if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                        path = buf.value
                finally:
                    kernel32.CloseHandle(handle)
        return PcState(idle, path)


class FakePc:
    """测试用：可改的空闲秒数和前台程序。"""

    def __init__(self, idle: float | None = 0.0, foreground: str = "", reason: str | None = None):
        self.state = PcState(idle, foreground)
        self.reason = reason

    def available(self) -> str | None:
        return self.reason

    def read(self) -> PcState:
        return self.state
