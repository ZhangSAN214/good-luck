"""游戏模式的真实来源（NVIDIA 驱动 / 进程列表）：只在 Windows + N 卡上真正可用，CI 上从没跑过。
这里注入假的 pynvml 模块，检查读数怎么合并、自己的进程怎么排除、驱动出错时只是「读不到」不抛错。"""

from __future__ import annotations

import os
import sys
import types
from dataclasses import dataclass

import pytest

from roundtable.core.gamemode import GpuReading, NvmlGpu, WindowsProcesses

MB = 1024 * 1024


@dataclass
class Proc:
    pid: int
    usedGpuMemory: object  # noqa: N815 - 和驱动返回的字段同名


class Util:
    def __init__(self, gpu):
        self.gpu = gpu


def fake_nvml(devices, *, init_error=None, count_error=None):
    """devices: [(占用 %, compute 进程列表, graphics 进程列表 | 异常)]。"""
    mod = types.ModuleType("pynvml")
    mod.inits = 0

    def nvmlInit():  # noqa: N802
        mod.inits += 1
        if init_error:
            raise init_error

    def count():
        if count_error:
            raise count_error
        return len(devices)

    def graphics(handle):
        procs = devices[handle][2]
        if isinstance(procs, Exception):
            raise procs
        return procs

    mod.nvmlInit = nvmlInit
    mod.nvmlDeviceGetCount = count
    mod.nvmlDeviceGetHandleByIndex = lambda i: i
    mod.nvmlDeviceGetUtilizationRates = lambda h: Util(devices[h][0])
    mod.nvmlDeviceGetComputeRunningProcesses = lambda h: devices[h][1]
    mod.nvmlDeviceGetGraphicsRunningProcesses = graphics
    return mod


@pytest.fixture
def install(monkeypatch):
    def _install(mod):
        monkeypatch.setitem(sys.modules, "pynvml", mod)
        return mod

    return _install


def test_without_pynvml_reports_reason_and_never_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "pynvml", None)  # import 时报 ImportError
    gpu = NvmlGpu(["python.exe"])
    reading = gpu.read()
    assert reading == GpuReading(reason="没装 nvidia-ml-py（只在 Windows 电脑上装）")
    assert gpu.read() == reading  # 之后不再重试导入


def test_init_failure_is_remembered_and_not_retried(install):
    mod = install(fake_nvml([], init_error=RuntimeError("NVML_ERROR_DRIVER_NOT_LOADED")))
    gpu = NvmlGpu([])
    first = gpu.read()
    assert first.util_pct is None and first.reason == "读不到 NVIDIA 显卡（RuntimeError）"
    gpu.read()
    assert mod.inits == 1


def test_takes_max_util_and_sums_other_programs_vram(install):
    install(
        fake_nvml(
            [
                (30, [Proc(101, 512 * MB)], [Proc(102, 256 * MB)]),
                (85, [], [Proc(103, 1024 * MB)]),
            ]
        )
    )
    gpu = NvmlGpu([], own_pid=None)
    reading = gpu.read()
    assert reading.util_pct == 85.0
    assert reading.others_mb == pytest.approx(512 + 256 + 1024)
    assert reading.reason is None


def test_own_pid_and_own_process_names_are_excluded(install):
    me = os.getpid()
    install(fake_nvml([(50, [Proc(me, 2048 * MB), Proc(2**22 + 7, 100 * MB)], [])]))
    own_name = __import__("psutil").Process(me).name()
    # 按进程号排除自己
    assert NvmlGpu([], own_pid=me).read().others_mb == pytest.approx(100)
    # 按程序名排除（大小写不敏感）：同名的进程不算「别人在用」
    install(fake_nvml([(50, [Proc(me, 2048 * MB)], [])]))
    assert NvmlGpu([own_name.upper()], own_pid=None).read().others_mb == 0.0


def test_driver_without_per_process_memory_means_unknown(install):
    # Windows 普通显示模式下 usedGpuMemory 常常是 None：不能当成 0（那样会以为没人在用）
    install(fake_nvml([(10, [Proc(5, None)], RuntimeError("not supported"))]))
    reading = NvmlGpu([]).read()
    assert reading.util_pct == 10.0 and reading.others_mb is None


def test_no_devices_reads_as_unknown(install):
    install(fake_nvml([]))
    assert NvmlGpu([]).read() == GpuReading(None, None)


def test_driver_error_during_read_is_a_reason_not_an_exception(install):
    install(fake_nvml([], count_error=OSError("gone")))
    reading = NvmlGpu([]).read()
    assert reading.util_pct is None and reading.reason == "读显卡出错（OSError）"


def test_windows_processes_lists_running_executables():
    paths = WindowsProcesses().paths()
    assert paths and all(isinstance(p, str) and p for p in paths)
    exe = os.path.realpath(sys.executable)
    assert any(os.path.realpath(p) == exe for p in paths)


@pytest.mark.skipif(sys.platform == "win32", reason="非 Windows 上的行为")
def test_fullscreen_is_false_off_windows():
    assert WindowsProcesses().fullscreen() is False
