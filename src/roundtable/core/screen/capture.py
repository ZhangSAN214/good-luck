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
import os
import re
import secrets
import shutil
import string
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Protocol

SIGNATURE_SIZE = (32, 18)  # 判断画面变没变：缩成 32×18 的灰度图逐点比
ADB_TIMEOUT_S = 8.0
ADB_START_TIMEOUT_S = 25.0  # 第一次启动 adb 后台服务较慢（Windows 上常要 5～15 秒）


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

    def __init__(self, ignore: tuple[str, ...] | list[str] = ()) -> None:
        self._dpi_done = False
        self.ignore = tuple(ignore)  # 窗口列表里不列的标题（配置 chat.watch.ignore_windows）

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
            out += [SourceInfo(f"pc:{h}", "window", t) for h, t in _win_windows(self.ignore)]
        return out

    def foreground(self, source_id: str) -> str:
        if sys.platform != "win32":
            return ""
        try:
            return _win_foreground()
        except Exception:  # noqa: BLE001 - 拿不到就当没有
            return ""

    def title(self, source_id: str) -> str:
        """投的那个窗口现在的标题（浏览器里就是正在看的视频 / 网页标题）；整屏和拿不到时为空。"""
        if sys.platform != "win32" or not source_id.startswith("pc:"):
            return ""
        try:
            return _win_title(int(source_id.removeprefix("pc:")))
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


# 系统后台、显卡浮层、输入法之类的窗口（标题含这些字就不列，不分大小写；配置可加）
NOISE_WINDOWS = (
    "Program Manager",
    "Windows 输入体验",
    "Microsoft Text Input Application",
    "NVIDIA GeForce Overlay",
    "no_active_wnd",
    "AsHotplugCtrl",
)
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
MIN_WINDOW_SIDE = 120  # 比这还小的窗口不列（后台占位的小窗口）


def keep_window(
    title: str,
    *,
    ex_style: int,
    owned: bool,
    cloaked: bool,
    width: int,
    height: int,
    ignore: tuple[str, ...] | list[str] = (),
) -> bool:
    """这个顶层窗口值不值得列出来（和任务栏 / Alt+Tab 里看到的差不多）。"""
    title = title.strip()
    if not title or cloaked or owned:
        return False
    if ex_style & WS_EX_TOOLWINDOW and not ex_style & WS_EX_APPWINDOW:
        return False  # 工具窗口、浮层
    if ex_style & WS_EX_NOACTIVATE and not ex_style & WS_EX_APPWINDOW:
        return False  # 点不到的浮层（显卡、录屏工具的叠加层）
    if min(width, height) < MIN_WINDOW_SIDE:
        return False
    low = title.lower()
    return not any(n.lower() in low for n in (*NOISE_WINDOWS, *ignore) if n)


def _win_windows(ignore: tuple[str, ...] | list[str] = ()) -> list[tuple[int, str]]:
    """可见、有标题、没最小化、不是工具窗口 / 浮层 / 系统后台的顶层窗口。"""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        dwm = ctypes.windll.dwmapi
    except OSError:  # 极老的系统没有
        dwm = None
    found: list[tuple[int, str]] = []
    user32.GetWindowLongW.restype = ctypes.c_long

    def cloaked(hwnd: int) -> bool:
        if dwm is None:
            return False
        value = ctypes.c_int(0)
        res = dwm.DwmGetWindowAttribute(
            wintypes.HWND(hwnd), 14, ctypes.byref(value), ctypes.sizeof(value)
        )  # DWMWA_CLOAKED：被系统藏起来的窗口（后台 UWP 应用等）
        return res == 0 and value.value != 0

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd: int, _: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        keep = keep_window(
            buf.value,
            ex_style=user32.GetWindowLongW(hwnd, -20) & 0xFFFFFFFF,  # GWL_EXSTYLE
            owned=bool(user32.GetWindow(hwnd, 4)),  # GW_OWNER：弹出的小窗、对话框
            cloaked=cloaked(hwnd),
            width=rect.right - rect.left,
            height=rect.bottom - rect.top,
            ignore=ignore,
        )
        if keep:
            found.append((int(hwnd), buf.value.strip()[:80]))
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


def _win_title(hwnd: int) -> str:
    import ctypes

    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


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
_HOST = re.compile(r"^[A-Za-z0-9.\-]{1,100}$")
_CODE = re.compile(r"^\d{6}$")
_MDNS_LINE = re.compile(
    r"^(\S+)\s+(_adb-tls-(?:pairing|connect)\._tcp)\.?\s+([A-Za-z0-9.\-]+):(\d{1,5})\s*$"
)
_QR_ALPHABET = string.ascii_lowercase + string.digits


def qr_pairing_payload(name: str, password: str) -> str:
    """安卓「使用二维码配对设备」扫的内容（和 Android Studio 的格式相同）。"""
    return f"WIFI:T:ADB;S:{name};P:{password};;"


def new_qr_pairing() -> tuple[str, str]:
    """随机生成一组二维码配对用的服务名和密码（只含小写字母和数字，不用转义）。"""
    pick = lambda n: "".join(secrets.choice(_QR_ALPHABET) for _ in range(n))  # noqa: E731
    return f"roundtable-{pick(8)}", pick(12)


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

    # --- 无线调试：配对 / 连接（只建立 adb 连接，不读写手机上的任何东西） ---------------------

    @staticmethod
    def _addr(host: str, port: int) -> str:
        host = (host or "").strip()
        if not _HOST.match(host) or not 1 <= int(port) <= 65535:
            raise ScreenUnavailable("IP 地址或端口不对")
        return f"{host}:{int(port)}"

    def _text(self, cmd: list[str]) -> str:
        return self.run(cmd).decode("utf-8", "replace").strip()

    def _ready(self) -> str:
        """找到 adb 并确保后台服务在跑；不行就给出能照着做的原因。"""
        exe = self._exe()
        if exe is None:
            raise ScreenUnavailable(self.available() or "没有 adb")
        try:
            self.run([exe, "start-server"])
        except ScreenUnavailable as e:
            raise ScreenUnavailable(
                f"电脑上的 adb 启动不了（{e}）。在命令行运行 adb start-server 看报错；"
                "被别的手机助手占用时先关掉它们"
            ) from None
        return exe

    def pair(self, host: str, port: int, code: str) -> str:
        """手机「无线调试 → 使用配对码配对设备」里显示的 IP:端口 和 6 位配对码。"""
        if not _CODE.match((code or "").strip()):
            raise ScreenUnavailable("配对码是 6 位数字")
        addr = self._addr(host, port)
        exe = self._ready()
        try:
            out = self._text([exe, "pair", addr, code.strip()])
        except ScreenUnavailable:
            out = ""  # 配对失败时 adb 也会返回非零
        if "Successfully paired" not in out:
            raise ScreenUnavailable("配对没成功：检查配对码和端口（配对码弹窗关掉后会失效）")
        return "配对成功"

    def mdns(self) -> list[tuple[str, str, str, int]]:
        """局域网里开着无线调试的设备：(服务名, pairing / connect, IP, 端口)。"""
        exe = self._exe()
        if exe is None:
            return []
        try:
            out = self._text([exe, "mdns", "services"])
        except ScreenUnavailable:
            return []
        found = []
        for line in out.splitlines():
            m = _MDNS_LINE.match(line.strip())
            if m and _HOST.match(m.group(3)) and 1 <= int(m.group(4)) <= 65535:
                kind = "pairing" if "pairing" in m.group(2) else "connect"
                found.append((m.group(1), kind, m.group(3), int(m.group(4))))
        return found

    def discover(self, host: str = "") -> list[str]:
        """同一个 Wi-Fi 里已配对、开着无线调试的设备（adb mdns）；给了 host 只找这台。"""
        found = {f"{h}:{p}" for _, kind, h, p in self.mdns() if kind == "connect"}
        return sorted(a for a in found if not host or a.rsplit(":", 1)[0] == host)

    def mdns_ok(self) -> bool:
        """adb 的局域网发现能不能用（扫码配对靠它）。"""
        exe = self._exe()
        if exe is None:
            return False
        try:
            out = self._text([exe, "mdns", "check"]).lower()
        except ScreenUnavailable:
            return False
        return "unavailable" not in out and "error" not in out

    def qr_ready(self) -> None:
        """扫码配对前的检查：adb 在跑、局域网发现可用（不可用时重启一次 adb 服务再看）。"""
        exe = self._ready()
        if self.mdns_ok():
            return
        # 老的 adb 服务可能是不带 ADB_MDNS_OPENSCREEN 启动的：重启一次（_run 会带上这个开关）
        with contextlib.suppress(ScreenUnavailable):
            self.run([exe, "kill-server"])
        self._ready()
        if not self.mdns_ok():
            raise ScreenUnavailable(
                "这台电脑的 adb 不支持扫码配对（局域网发现用不了）。把 Android 平台工具更新到"
                "最新版，或改用下面的「配对码」方式"
            )

    def qr_pair(self, name: str, password: str) -> str | None:
        """手机扫了二维码后会在局域网里广播同名的配对服务：找到就配对，返回配对用的地址。

        还没扫（没找到）返回 None；找到了但配对失败时抛错。
        """
        exe = self._ready()
        hit = next(
            ((h, p) for n, kind, h, p in self.mdns() if kind == "pairing" and n == name), None
        )
        if hit is None:
            return None
        addr = self._addr(*hit)
        try:
            out = self._text([exe, "pair", addr, password])
        except ScreenUnavailable:
            out = ""
        if "Successfully paired" not in out:
            raise ScreenUnavailable("配对没成功：手机上点「重试」或重新生成二维码")
        return addr

    def _try_connect(self, exe: str, addr: str) -> bool:
        try:
            out = self._text([exe, "connect", addr])
        except ScreenUnavailable:
            return False  # 连到配对端口时 adb 常常一直等，超时就算没连上
        return "connected to" in out and "cannot" not in out and "failed" not in out

    def connect(self, host: str = "", port: int = 0) -> list[str]:
        """连上设备。返回连上的地址。

        - 什么都不填：自动找已配对的设备。
        - 只填 IP（端口 0）：在局域网里找这台设备的连接端口。
        - 填了端口但连不上（常见：填成了配对弹窗里的配对端口）：自动找这台设备真正的连接端口再试。
        """
        if host and port:
            first = [self._addr(host, port)]
        elif host:
            first = []
            self._addr(host, 1)  # 只校验 IP
        else:
            first = []
        exe = self._ready()
        done = [a for a in first if self._try_connect(exe, a)]
        if done:
            return done
        found = [a for a in self.discover(host.strip()) if a not in first]
        done = [a for a in found if self._try_connect(exe, a)]
        if done:
            return done
        if first:
            raise ScreenUnavailable(
                "连接没成功：连接端口和配对端口不一样。请填无线调试主页面「IP 地址和端口」"
                "那一行的端口（不是配对弹窗里的），或者端口留空让圆桌自己找"
            )
        raise ScreenUnavailable(
            "没找到设备：确认手机和电脑在同一个 Wi-Fi、无线调试开着；"
            "也可以填无线调试页面上「IP 地址和端口」那一行"
        )

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
    starting = "start-server" in cmd
    # 新版 adb 自带的局域网发现（扫码配对要用）；只影响由这条命令启动的 adb 服务
    env = {**os.environ, "ADB_MDNS_OPENSCREEN": "1"}
    try:
        done = subprocess.run(
            cmd,
            capture_output=True,
            timeout=ADB_START_TIMEOUT_S if starting else ADB_TIMEOUT_S,
            check=False,
            creationflags=flags,
            env=env,
        )
    except OSError:
        raise ScreenUnavailable("adb 运行不了（路径不对，或被杀毒软件拦了）") from None
    except subprocess.TimeoutExpired:
        if starting:
            raise ScreenUnavailable("adb 服务启动超时") from None
        raise ScreenUnavailable("adb 没有响应（手机断开了，或端口不对？）") from None
    if done.returncode != 0:
        if starting:
            raise ScreenUnavailable("adb 服务启动失败，可能端口 5037 被别的手机助手占用") from None
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
        self.window_title = ""
        self.reason = reason
        self.grabs = 0
        self.fail: str | None = None

    def available(self) -> str | None:
        return self.reason

    def sources(self) -> list[SourceInfo]:
        return [] if self.reason else list(self.infos)

    def foreground(self, source_id: str) -> str:
        return self.fg

    def title(self, source_id: str) -> str:
        return self.window_title

    def grab(self, source_id: str, max_side: int, quality: int) -> Frame:
        if self.fail:
            raise ScreenUnavailable(self.fail)
        if not self.images:
            raise ScreenUnavailable("没有画面")
        image = self.images[min(self.grabs, len(self.images) - 1)]
        self.grabs += 1
        return encode_frame(image, max_side, quality, self.fg)
