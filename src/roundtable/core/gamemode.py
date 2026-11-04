"""游戏模式（docs/GAMEMODE.md）：你在玩游戏、或别的程序在用显卡时，圆桌让出显卡。

- **忙的判断**（任一）：网页上的手动开关；在跑游戏（「关于你」的游戏表 + Steam / Epic 装的，不管在
  不在前台）；前台是全屏程序；显卡占用、或别的程序占的显存超过门槛（NVML，读 NVIDIA 驱动）。
  圆桌自己的显卡活（本地出图、本地配音）正在做和刚做完的 ``own_grace_seconds`` 内不看占用，
  免得把自己当成「别人在用」。
- **忙时**：路由不调用 ``gpu: true`` 的渠道（换下一个模型），见 ``ChannelRouter.set_gpu_guard``；
  要等显卡的活（朋友圈配图）放进排队，**连续空闲 ``idle_minutes`` 分钟**后逐个补跑。
  圆桌自己的 CPU 降到最低优先级（只在 Windows 上；``cpu_when_busy: pause`` 时再停后台循环）。
- 读到的程序名只在内存里用来判断，不写数据库、不进日志；界面上只说「在玩：<游戏名>」这类一句话。
- 不在 Windows / 没有 NVIDIA 显卡时对应那项不可用，绝不抛错。测试中注入假的来源。
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
import time
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from roundtable.core.config.schema import GameModeRules
from roundtable.core.yourpc import game_of

log = logging.getLogger(__name__)

MANUAL_KEY = "gamemode_manual"  # app_settings：手动开关（"1" = 开）
QUEUE_FILE = "gpu_queue.json"  # 排队的显卡活（放在数据库旁边，不进 git）


class StillBusy(Exception):
    """补跑时显卡又忙了：这件放回队首，下次再试。"""


# --- 来源 ------------------------------------------------------------------------------


@dataclass(frozen=True)
class GpuReading:
    util_pct: float | None = None  # 显卡占用（%）；None = 读不到
    others_mb: float | None = None  # 别的程序占的显存（MB）；None = 驱动不给每个程序的显存
    reason: str | None = None  # 不可用时的原因（给用户看的中文）


class GpuSource(Protocol):
    def read(self) -> GpuReading: ...


class ProcessSource(Protocol):
    def paths(self) -> list[str]:
        """正在跑的程序的完整路径（读不到的跳过）。"""

    def fullscreen(self) -> bool:
        """前台窗口是不是全屏程序。"""


class NvmlGpu:
    """真正读 NVIDIA 驱动（nvidia-ml-py，只在 Windows 上装；没装或没有 N 卡时不可用）。"""

    def __init__(self, own_processes: list[str], own_pid: int | None = None) -> None:
        self._own_names = {n.lower() for n in own_processes}
        self._own_pid = own_pid
        self._nvml: Any = None
        self._failed: str | None = None

    def _init(self) -> Any:
        if self._nvml is None and self._failed is None:
            try:
                import pynvml  # type: ignore[import-not-found]

                pynvml.nvmlInit()
                self._nvml = pynvml
            except ImportError:
                self._failed = "没装 nvidia-ml-py（只在 Windows 电脑上装）"
            except Exception as exc:  # noqa: BLE001 - 没有 N 卡、驱动太旧：只是读不到
                self._failed = f"读不到 NVIDIA 显卡（{type(exc).__name__}）"
        return self._nvml

    def read(self) -> GpuReading:
        nvml = self._init()
        if nvml is None:
            return GpuReading(reason=self._failed)
        try:
            util: float | None = None
            others: float | None = None
            for i in range(nvml.nvmlDeviceGetCount()):
                handle = nvml.nvmlDeviceGetHandleByIndex(i)
                pct = float(nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
                util = pct if util is None else max(util, pct)
                mb = self._others_mb(nvml, handle)
                if mb is not None:
                    others = mb if others is None else others + mb
            return GpuReading(util, others)
        except Exception as exc:  # noqa: BLE001 - 驱动出错：这次读不到，下次再试
            return GpuReading(reason=f"读显卡出错（{type(exc).__name__}）")

    def _others_mb(self, nvml: Any, handle: Any) -> float | None:
        """别的程序占的显存。Windows 的普通显示模式下驱动常常不给每个程序的显存（返回 None）。"""
        import psutil

        procs: list[Any] = []
        for kind in ("Compute", "Graphics"):
            getter = f"nvmlDeviceGet{kind}RunningProcesses"
            with contextlib.suppress(Exception):
                procs += list(getattr(nvml, getter)(handle))
        total = 0.0
        known = False
        for proc in procs:
            used = getattr(proc, "usedGpuMemory", None)
            if not isinstance(used, int | float):
                continue
            known = True
            if proc.pid == self._own_pid:
                continue
            with contextlib.suppress(Exception):
                if psutil.Process(proc.pid).name().lower() in self._own_names:
                    continue
            total += used / 1024 / 1024
        return total if known else None


class WindowsProcesses:
    """正在跑的程序（psutil）和前台全屏（ctypes，只在 Windows 上）。"""

    def paths(self) -> list[str]:
        import psutil

        out: list[str] = []
        for proc in psutil.process_iter(["exe"]):
            exe = proc.info.get("exe")
            if exe:
                out.append(str(exe))
        return out

    def fullscreen(self) -> bool:
        if sys.platform != "win32":
            return False
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        name = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, name, 64)
        if name.value in ("Progman", "WorkerW", "Shell_TrayWnd"):  # 桌面、任务栏
            return False

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        monitor = user32.MonitorFromWindow(hwnd, 2)  # 最近的显示器
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False
        m = info.rcMonitor
        return (
            rect.left <= m.left
            and rect.top <= m.top
            and rect.right >= m.right
            and (rect.bottom >= m.bottom)
        )


class FakeGpu:
    def __init__(self, util: float | None = 0.0, others_mb: float | None = None, reason=None):
        self.reading = GpuReading(util, others_mb, reason)

    def read(self) -> GpuReading:
        return self.reading


class FakeProcesses:
    def __init__(self, paths: list[str] | None = None, fullscreen: bool = False) -> None:
        self.running = list(paths or [])
        self.full = fullscreen

    def paths(self) -> list[str]:
        return list(self.running)

    def fullscreen(self) -> bool:
        return self.full


# --- 排队 ------------------------------------------------------------------------------


@dataclass
class Job:
    kind: str
    label: str  # 给用户看的一句话（「克劳德的朋友圈配图」）
    payload: dict[str, Any]
    added: float  # 加入时刻（Unix 秒）

    def view(self) -> dict[str, Any]:
        return {"kind": self.kind, "label": self.label, "added": self.added}


Handler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class Status:
    busy: bool = False
    reasons: list[str] = field(default_factory=list)
    gpu: GpuReading = GpuReading()
    at: float = 0.0  # 什么时候看的（单调时钟）


class GameMode:
    def __init__(
        self,
        rules: GameModeRules,
        *,
        gpu: GpuSource | None = None,
        procs: ProcessSource | None = None,
        games: Callable[[], dict[str, str]] = dict,
        load: Callable[[str], str | None] = lambda _k: None,
        save: Callable[[str, str], None] = lambda _k, _v: None,
        queue_file: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
        set_priority: Callable[[bool], None] | None = None,
    ) -> None:
        self.rules = rules
        self._gpu = gpu if gpu is not None else NvmlGpu(rules.own_processes, _own_pid())
        self._procs = procs if procs is not None else WindowsProcesses()
        self._games = games
        self._load = load
        self._save = save
        self._queue_file = queue_file
        self._clock = clock
        self._wall = wall
        self._set_priority = set_priority if set_priority is not None else _windows_priority
        self._status: Status | None = None
        self._last_busy: float | None = None
        self._own_active = 0
        self._own_end: float | None = None
        self._lowered = False
        self._handlers: dict[str, Handler] = {}
        self._queue: list[Job] = self._read_queue()

    def set_games(self, games: Callable[[], dict[str, str]]) -> None:
        """游戏表的来源（服务启动时换成「关于你」的：配置里的 + 你自己加的）。"""
        self._games = games

    # --- 开关 --------------------------------------------------------------------------

    @property
    def manual(self) -> bool:
        return self._load(MANUAL_KEY) == "1"

    def set_manual(self, on: bool) -> dict[str, Any]:
        self._save(MANUAL_KEY, "1" if on else "")
        self.poll()
        return self.view()

    # --- 判断 --------------------------------------------------------------------------

    def _own_quiet(self, now: float) -> bool:
        """自己的显卡活没在做、也过了余量时间：这时才看显卡占用。"""
        if self._own_active:
            return False
        return self._own_end is None or now - self._own_end >= self.rules.own_grace_seconds

    def poll(self) -> Status:
        """看一次（几十到几百毫秒：要列一遍正在跑的程序）。"""
        rules = self.rules
        now = self._clock()
        reasons: list[str] = []
        reading = GpuReading()
        if self.manual:
            reasons.append("你手动开了游戏模式")
        if rules.auto:
            if rules.detect_games:
                game = self._game()
                if game:
                    reasons.append(f"在玩：{game}")
            if rules.detect_fullscreen:
                with contextlib.suppress(Exception):
                    if self._procs.fullscreen():
                        reasons.append("前台是全屏程序")
            reading = self._gpu.read()
            if self._own_quiet(now):
                util = reading.util_pct
                if rules.busy_util_pct and util is not None and util >= rules.busy_util_pct:
                    reasons.append(f"显卡占用 {util:.0f}%")
                mb = reading.others_mb
                if rules.busy_vram_mb and mb is not None and mb >= rules.busy_vram_mb:
                    reasons.append(f"别的程序占着 {mb:.0f} MB 显存")
        status = Status(bool(reasons), reasons, reading, now)
        if status.busy:
            self._last_busy = now
        self._status = status
        return status

    def _game(self) -> str | None:
        try:
            paths = self._procs.paths()
            table = self._games()
        except Exception:  # noqa: BLE001 - 列不出程序：这项当没有
            return None
        skip = {n.lower() for n in self.rules.not_games}
        for path in paths:
            game = game_of(path, table)
            if game and game.lower() not in skip:
                return game
        return None

    def status(self) -> Status:
        """最近一次的结果；太久没看就现看一次。"""
        status = self._status
        if status is None or self._clock() - status.at >= self.rules.poll_seconds:
            status = self.poll()
        return status

    def free_in(self) -> float:
        """还要等多少秒才能用显卡（0 = 现在就能用）。"""
        status = self.status()
        if status.busy:
            return self.rules.idle_minutes * 60
        if self._last_busy is None:
            return 0.0
        return max(0.0, self.rules.idle_minutes * 60 - (self._clock() - self._last_busy))

    def blocked(self) -> str | None:
        """现在不能用显卡的原因；能用时为 None。路由调用 ``gpu: true`` 的渠道前问它。"""
        status = self.status()
        if status.busy:
            return f"游戏模式：{status.reasons[0]}"
        wait = self.free_in()
        if wait > 0:
            return f"游戏模式：显卡刚空下来，再等约 {max(1, round(wait / 60))} 分钟"
        return None

    @contextlib.contextmanager
    def hold(self) -> Iterator[None]:
        """圆桌自己在用显卡（路由调用 gpu 渠道时包住）：这段时间和之后一会儿不看占用。"""
        self._own_active += 1
        try:
            yield
        finally:
            self._own_active -= 1
            self._own_end = self._clock()

    # --- CPU ---------------------------------------------------------------------------

    def background_paused(self) -> bool:
        """后台循环（生活、例会、管家、周报）这一轮要不要跳过。"""
        return self.rules.cpu_when_busy == "pause" and self.status().busy

    def apply_priority(self) -> bool:
        """忙时把圆桌自己降到最低 CPU 优先级，不忙时恢复。返回现在是不是降着。"""
        want = self.rules.cpu_when_busy != "normal" and self.status().busy
        if want != self._lowered:
            try:
                self._set_priority(want)
                self._lowered = want
            except Exception:  # noqa: BLE001 - 改不了优先级不影响别的
                log.warning("游戏模式：改 CPU 优先级失败")
        return self._lowered

    # --- 排队 --------------------------------------------------------------------------

    def register(self, kind: str, handler: Handler) -> None:
        self._handlers[kind] = handler

    def defer(self, kind: str, label: str, payload: dict[str, Any]) -> None:
        """放进排队（满了丢掉最早的一件）。"""
        self._queue.append(Job(kind, label, dict(payload), self._wall()))
        del self._queue[: max(0, len(self._queue) - self.rules.queue_max)]
        self._write_queue()

    def queue(self) -> list[Job]:
        return list(self._queue)

    async def drain_one(self) -> bool:
        """显卡能用时补跑最早的一件（没注册处理的先留着）。返回有没有跑。"""
        if self.blocked() is not None:
            return False
        for i, job in enumerate(self._queue):
            handler = self._handlers.get(job.kind)
            if handler is None:
                continue
            del self._queue[i]
            self._write_queue()
            try:
                await handler(job.payload)
            except StillBusy:
                self._queue.insert(0, job)
                self._write_queue()
                return False
            except Exception:  # noqa: BLE001 - 这件补跑失败就丢掉，不卡住后面的
                log.exception("游戏模式：补跑「%s」出错，已丢掉", job.label)
            return True
        return False

    def _read_queue(self) -> list[Job]:
        if self._queue_file is None:
            return []
        try:
            raw = json.loads(self._queue_file.read_text(encoding="utf-8"))
            return [
                Job(str(j["kind"]), str(j["label"]), dict(j["payload"]), float(j["added"]))
                for j in raw
            ]
        except FileNotFoundError:
            return []
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("游戏模式：排队文件读不了，从空的开始")
            return []

    def _write_queue(self) -> None:
        if self._queue_file is None:
            return
        try:
            self._queue_file.parent.mkdir(parents=True, exist_ok=True)
            data = [
                {"kind": j.kind, "label": j.label, "payload": j.payload, "added": j.added}
                for j in self._queue
            ]
            tmp = self._queue_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._queue_file)
        except OSError:
            log.warning("游戏模式：排队文件写不了")

    # --- 展示 --------------------------------------------------------------------------

    def view(self) -> dict[str, Any]:
        status = self.status()
        wait = self.free_in()
        return {
            "auto": self.rules.auto,
            "manual": self.manual,
            "busy": status.busy,
            "reasons": status.reasons,
            "gpu_free": wait <= 0,
            "free_in_s": round(wait),
            "idle_minutes": self.rules.idle_minutes,
            "gpu": {
                "util_pct": status.gpu.util_pct,
                "others_mb": None if status.gpu.others_mb is None else round(status.gpu.others_mb),
                "unavailable": status.gpu.reason,
            },
            "cpu_when_busy": self.rules.cpu_when_busy,
            "cpu_lowered": self._lowered,
            "queue": [j.view() for j in self._queue],
        }


def _own_pid() -> int | None:
    import os

    return os.getpid()


def _windows_priority(low: bool) -> None:
    """只在 Windows 上改：别的系统降了以后普通用户升不回来，干脆不动。"""
    if sys.platform != "win32":
        return
    import psutil

    psutil.Process().nice(psutil.IDLE_PRIORITY_CLASS if low else psutil.NORMAL_PRIORITY_CLASS)  # type: ignore[attr-defined]
