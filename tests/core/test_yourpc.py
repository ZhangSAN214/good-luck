"""「关于你」的电脑状态：认游戏、不是 Windows 时不可用，
以及 Windows 读法（假的 user32 / kernel32）。"""

from __future__ import annotations

import ctypes
import sys

import pytest

from roundtable.core.yourpc import FakePc, PcState, WindowsPc, game_of

GAMES = {"YuanShen.exe": "原神", "eldenring.exe": "艾尔登法环"}


@pytest.mark.parametrize(
    ("path", "game"),
    [
        (r"C:\Program Files\Genshin Impact\YuanShen.exe", "原神"),
        (r"D:\games\ELDENRING.EXE", "艾尔登法环"),  # 文件名不分大小写
        (r"D:\Steam\steamapps\common\Hades II\Hades2.exe", "Hades II"),
        (r"C:\Program Files\Epic Games\Fortnite\FortniteClient.exe", "Fortnite"),
        ("/home/me/.steam/steamapps/common/Celeste/Celeste", "Celeste"),
        (r"C:\Windows\notepad.exe", None),  # 不是游戏什么都不报
        (r"C:\Users\me\steamapps\x.exe", None),  # 只有 common 下面的子文件夹才算
        ("", None),
    ],
)
def test_game_of(path, game):
    assert game_of(path, GAMES) == game


def test_not_windows_is_unavailable_and_reads_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    pc = WindowsPc()
    assert pc.available() == "只在 Windows 电脑上能读"
    assert pc.read() == PcState()


def test_fake_pc_can_be_changed():
    pc = FakePc(idle=30.0, foreground="game.exe", reason=None)
    assert pc.available() is None and pc.read() == PcState(30.0, "game.exe")


class _User32:
    def __init__(self, has_input: bool = True) -> None:
        self.has_input = has_input

    def GetLastInputInfo(self, ref) -> int:  # noqa: N802 - Windows API 名
        return 1 if self.has_input else 0  # 最后一次输入的 dwTime 保持 0

    def GetForegroundWindow(self) -> int:  # noqa: N802
        return 42

    def GetWindowThreadProcessId(self, hwnd, ref) -> int:  # noqa: N802
        assert hwnd == 42
        return 1


class _Kernel32:
    def __init__(self, ticks: int, path: str | None, handle: int = 7) -> None:
        self.ticks, self.path, self.handle = ticks, path, handle
        self.opened: list[int] = []
        self.closed: list[int] = []

    def GetTickCount(self) -> int:  # noqa: N802
        return self.ticks

    def OpenProcess(self, access, inherit, pid) -> int:  # noqa: N802
        self.opened.append(access)
        return self.handle

    def QueryFullProcessImageNameW(self, handle, flags, buf, size_ref) -> int:  # noqa: N802
        if self.path is None:
            return 0
        buf.value = self.path
        return 1

    def CloseHandle(self, handle) -> int:  # noqa: N802
        self.closed.append(handle)
        return 1


def _windows(monkeypatch, user32, kernel32) -> None:
    class WinDLL:
        pass

    dll = WinDLL()
    dll.user32, dll.kernel32 = user32, kernel32
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", dll, raising=False)


def test_windows_reads_idle_seconds_and_foreground_program(monkeypatch):
    path = r"D:\Steam\steamapps\common\Hades II\Hades2.exe"
    kernel32 = _Kernel32(ticks=125_500, path=path)
    _windows(monkeypatch, _User32(), kernel32)
    pc = WindowsPc()
    assert pc.available() is None
    state = pc.read()
    assert state.idle_seconds == pytest.approx(125.5) and state.foreground == path
    assert game_of(state.foreground, GAMES) == "Hades II"
    # 只申请「查询有限信息」的权限，用完就关
    assert kernel32.opened == [0x1000] and kernel32.closed == [7]


def test_windows_tick_counter_wraparound_still_gives_a_small_idle_time(monkeypatch):
    # GetTickCount 约 49.7 天归零一次：最后输入在归零前、现在在归零后
    class Before(_User32):
        def GetLastInputInfo(self, ref) -> int:  # noqa: N802
            ref._obj.dwTime = 0xFFFFFFFF - 999  # 归零前 1 秒
            return 1

    _windows(monkeypatch, Before(), _Kernel32(ticks=1000, path=""))
    assert WindowsPc().read().idle_seconds == pytest.approx(2.0)


def test_windows_without_input_info_or_process_access_reports_nothing(monkeypatch):
    kernel32 = _Kernel32(ticks=5000, path=None, handle=0)
    _windows(monkeypatch, _User32(has_input=False), kernel32)
    assert WindowsPc().read() == PcState(None, "")
    assert kernel32.closed == []  # 没打开就不关


def test_windows_name_query_failure_still_closes_the_handle(monkeypatch):
    kernel32 = _Kernel32(ticks=5000, path=None, handle=9)
    _windows(monkeypatch, _User32(), kernel32)
    assert WindowsPc().read() == PcState(5.0, "")
    assert kernel32.closed == [9]
