"""投屏的画面来源：电脑屏幕（整屏或某个窗口）、安卓手机 / 平板（adb 截屏）。

- 只在用户点了「开始投屏」后、按间隔截一张；截下的画面只在内存里（最近一张给网页预览、
  画面变了时交给识图模型），不写盘、不进数据库。
- 敏感判断：电脑看前台窗口的标题和进程名，安卓看前台应用的包名，命中配置里的关键字
  （支付、银行、密码管理器……）就暂停，不截这一张。
- 电脑截屏用 Pillow 的 ImageGrab（Windows / macOS）；窗口列表、前台窗口只在 Windows 上有
  （ctypes 调系统接口，不加依赖）。安卓用 adb（需要用户自己装好、手机开 USB / 无线调试）。
- 没装 Pillow、没有 adb、不在支持的系统上：available() 返回原因，绝不抛错。
测试中注入 FakeScreenSource。
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol

SIGNATURE_SIZE = (32, 18)  # 判断画面变没变：缩成 32×18 的灰度图逐点比
ADB_TIMEOUT_S = 8.0


class ScreenUnavailable(RuntimeError):
    """截不到画面（来源断开、窗口关了、系统不支持）。消息给用户看，不含敏感内容。"""


@dataclass(frozen=True)
class SourceInfo:
    """一个可以投屏的来源。id：pc（整屏）、pc:<窗口句柄>、android:<设备序列号>。"""

    id: str
    kind: str  # pc / window / android
    title: str


@dataclass(frozen=True)
class Frame:
    """一张截图：给模型和网页用的 JPEG（已缩小）、用来比对变化的小灰度图、前台是谁。"""

    jpeg: bytes = field(repr=False)
    width: int
    height: int
    signature: bytes = field(repr=False)
    foreground: str = ""  # 前台窗口标题 / 进程名，或安卓前台包名（判断敏感用，不存）


class ScreenSource(Protocol):
    def available(self) -> str | None:
        """能用时返回 None，不能用时返回原因。"""

    def sources(self) -> list[SourceInfo]: ...

    def foreground(self, source_id: str) -> str:
        """前台窗口 / 应用（判断敏感用）；拿不到返回空串。"""

    def grab(self, source_id: str, max_side: int, quality: int) -> Frame: ...


def _pil() -> Any:
    try:
        from PIL import Image
    except ImportError:
        return None
    return Image


def encode_frame(image: Any, max_side: int, quality: int, foreground: str = "") -> Frame:
    """Pillow 图片 → Frame：缩到最长边 max_side 的 JPEG，另算比对用的小灰度图。"""
    img = image.convert("RGB")
    k = min(1.0, max_side / max(img.width, img.height, 1))
    if k < 1.0:
        img = img.resize((max(1, round(img.width * k)), max(1, round(img.height * k))))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality, optimize=True)
    sig = img.convert("L").resize(SIGNATURE_SIZE).tobytes()
    return Frame(buf.getvalue(), img.width, img.height, sig, foreground)


def change_ratio(a: bytes | None, b: bytes) -> float:
    """两张小灰度图平均差多少（0 = 一样，1 = 黑白颠倒）。没有上一张时算 1。"""
    if not a or len(a) != len(b):
        return 1.0
    return sum(abs(x - y) for x, y in zip(a, b, strict=True)) / (255 * len(b))


def is_sensitive(foreground: str, keywords: list[str]) -> str | None:
    """前台窗口 / 应用命中敏感关键字（不分大小写）时返回命中的关键字。"""
    low = foreground.lower()
    for k in keywords:
        if k and k.lower() in low:
            return k
    return None


# ---------- 电脑 ----------


class PcSource:
    """电脑屏幕：整屏（所有显示器拼在一起）；Windows 上还能选某个窗口。"""

    def __init__(self) -> None:
        self._dpi_done = False

    def available(self) -> str | None:
        if sys.platform not in ("win32", "darwin"):
            return "电脑截屏只支持 Windows 和 macOS"
        if _pil() is None:
            return '没有安装 Pillow（pip install -e ".[dev]"）'
        return None

    def sources(self) -> list[SourceInfo]:
        if self.available():
            return []
        out = [SourceInfo("pc", "pc", "整个屏幕")]
        if sys.platform == "win32":
            out += [SourceInfo(f"pc:{h}", "window", t) for h, t in _win_windows()]
        return out

    def foreground(self, source_id: str) -> str:
        if sys.platform != "win32":
            return ""
        try:
            return _win_foreground()
        except Exception:  # noqa: BLE001 - 拿不到就当没有
            return ""

    def grab(self, source_id: str, max_side: int, quality: int) -> Frame:
        reason = self.available()
        if reason:
            raise ScreenUnavailable(reason)
        from PIL import ImageGrab

        self._dpi_aware()
        bbox = None
        if source_id.startswith("pc:"):
            bbox = _win_rect(int(source_id[3:]))
            if bbox is None:
                raise ScreenUnavailable("这个窗口已经关了或被最小化了")
        try:
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
        except OSError:
            raise ScreenUnavailable("截屏失败（屏幕可能锁住了）") from None
        return encode_frame(image, max_side, quality, self.foreground(source_id))

    def _dpi_aware(self) -> None:
        """Windows 缩放 125% / 150% 时窗口坐标和截图对得上。"""
        if self._dpi_done or sys.platform != "win32":
            return
        self._dpi_done = True
        import ctypes

        with contextlib.suppress(Exception):  # 老系统没有这个接口
            ctypes.windll.shcore.SetProcessDpiAwareness(2)


def _win_windows() -> list[tuple[int, str]]:
    """可见、有标题、没最小化的顶层窗口。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    found: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value.strip()
        if title and title not in ("Program Manager", "Windows 输入体验"):
            found.append((int(hwnd), title[:80]))
        return True

    user32.EnumWindows(each, 0)
    return found[:40]


def _win_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    # 优先用 DWM 的实际边框（不含阴影），失败时用 GetWindowRect
    try:
        ok = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 9, ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if ok != 0:
            raise OSError
    except Exception:  # noqa: BLE001
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    if rect.right - rect.left < 20 or rect.bottom - rect.top < 20:
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _win_foreground() -> str:
    """前台窗口的「标题 | 进程名」。"""
    import ctypes
    from ctypes import wintypes

    import psutil

    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).name()
    except Exception:  # noqa: BLE001
        exe = ""
    return f"{buf.value} | {exe}"


# ---------- 安卓（adb） ----------

_FOCUS = re.compile(r"mCurrentFocus=Window\{[^}]*\s([A-Za-z0-9_.]+)/")
_FOCUS_APP = re.compile(r"mFocusedApp=.*?\s([A-Za-z0-9_.]+)/")
_SERIAL = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")


class AdbSource:
    """安卓手机 / 平板：adb exec-out screencap。只读画面，不发任何点按、输入。"""

    def __init__(self, adb: str = "adb", runner: Any = None) -> None:
        self.adb = adb
        self.run = runner or _run

    def _exe(self) -> str | None:
        return shutil.which(self.adb)

    def available(self) -> str | None:
        if self._exe() is None:
            return "没有找到 adb（装 Android 平台工具并加入 PATH，手机打开 USB / 无线调试）"
        return None

    def sources(self) -> list[SourceInfo]:
        exe = self._exe()
        if exe is None:
            return []
        try:
            out = self.run([exe, "devices", "-l"]).decode("utf-8", "replace")
        except ScreenUnavailable:
            return []
        found = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2 or parts[1] != "device" or not _SERIAL.match(parts[0]):
                continue
            model = next((p[6:] for p in parts if p.startswith("model:")), "")
            title = model.replace("_", " ") or parts[0]
            found.append(SourceInfo(f"android:{parts[0]}", "android", title))
        return found

    def _serial(self, source_id: str) -> str:
        serial = source_id.removeprefix("android:")
        if not _SERIAL.match(serial):
            raise ScreenUnavailable("设备编号不对")
        return serial

    def foreground(self, source_id: str) -> str:
        exe = self._exe()
        if exe is None:
            return ""
        try:
            out = self.run(
                [exe, "-s", self._serial(source_id), "shell", "dumpsys", "window"]
            ).decode("utf-8", "replace")
        except ScreenUnavailable:
            return ""
        m = _FOCUS.search(out) or _FOCUS_APP.search(out)
        return m.group(1) if m else ""

    def grab(self, source_id: str, max_side: int, quality: int) -> Frame:
        exe = self._exe()
        if exe is None:
            raise ScreenUnavailable(self.available() or "没有 adb")
        Image = _pil()
        if Image is None:
            raise ScreenUnavailable('没有安装 Pillow（pip install -e ".[dev]"）')
        serial = self._serial(source_id)
        png = self.run([exe, "-s", serial, "exec-out", "screencap", "-p"])
        try:
            image = Image.open(io.BytesIO(png))
            image.load()
        except Exception:  # noqa: BLE001
            raise ScreenUnavailable("手机没有返回画面（可能锁屏了或断开了）") from None
        return encode_frame(image, max_side, quality, self.foreground(source_id))


def _run(cmd: list[str]) -> bytes:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # Windows 上不弹黑窗口
    try:
        done = subprocess.run(
            cmd, capture_output=True, timeout=ADB_TIMEOUT_S, check=False, creationflags=flags
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ScreenUnavailable("adb 没有响应（手机断开了？）") from None
    if done.returncode != 0:
        raise ScreenUnavailable("adb 出错（手机断开了，或没在手机上允许调试）")
    return done.stdout


# ---------- 测试 ----------


class FakeScreenSource:
    """测试用：一组来源，grab 依次返回给定的 Pillow 图片（用完重复最后一张）。"""

    def __init__(
        self,
        images: list[Any] | None = None,
        *,
        sources: list[SourceInfo] | None = None,
        foreground: str = "",
        reason: str | None = None,
    ) -> None:
        self.images = list(images or [])
        self.infos = sources if sources is not None else [SourceInfo("pc", "pc", "整个屏幕")]
        self.fg = foreground
        self.reason = reason
        self.grabs = 0
        self.fail: str | None = None

    def available(self) -> str | None:
        return self.reason

    def sources(self) -> list[SourceInfo]:
        return [] if self.reason else list(self.infos)

    def foreground(self, source_id: str) -> str:
        return self.fg

    def grab(self, source_id: str, max_side: int, quality: int) -> Frame:
        if self.fail:
            raise ScreenUnavailable(self.fail)
        if not self.images:
            raise ScreenUnavailable("没有画面")
        image = self.images[min(self.grabs, len(self.images) - 1)]
        self.grabs += 1
        return encode_frame(image, max_side, quality, self.fg)
