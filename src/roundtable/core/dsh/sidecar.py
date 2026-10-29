"""dsh 进程（边车）：启动、等它报出网页地址、读输出、停止。

- dsh 网页启动后会打印一行 ``dsh web: http://127.0.0.1:<端口>/?token=<一次性口令>``；
  看到这一行才算启动好。口令只留在内存里，日志里替换成 ``***``。
- 停止：先礼貌结束，几秒后还没退就强制结束；
  Windows 上连同子进程（终端窗格的 PowerShell 等）一起结束。
- 进程自己退出时记下退出码和最后几行输出，界面上能看到为什么停了。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

State = Literal["stopped", "starting", "running", "stopping"]

_URL = re.compile(r"(https?://127\.0\.0\.1:\d+/\S*)")
_TOKEN = re.compile(r"(token=)[^\s&]+")
LOG_LINES = 200


@dataclass(frozen=True)
class Exit:
    code: int | None
    why: str
    at: float


class Sidecar:
    def __init__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: dict[str, str],
        start_timeout_s: float,
        secrets: Sequence[str] = (),
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.start_timeout_s = start_timeout_s
        self._secrets = [s for s in secrets if s]
        self.clock = clock
        self.state: State = "stopped"
        self.url: str | None = None
        self.started_at: float | None = None
        self.last_exit: Exit | None = None
        self.log: deque[str] = deque(maxlen=LOG_LINES)
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def _clean(self, line: str) -> str:
        line = _TOKEN.sub(r"\1***", line)
        for s in self._secrets:
            line = line.replace(s, "***")
        return line.rstrip()

    async def start(self) -> str:
        """启动并等到网页地址出现；返回地址（带口令）。失败时抛 RuntimeError（已脱敏）。"""
        if self.state in ("starting", "running"):
            if self.url:
                return self.url
            raise RuntimeError("dsh 正在启动，请稍等")
        self.cwd.mkdir(parents=True, exist_ok=True)
        self.state, self.url, self._ready = "starting", None, asyncio.Event()
        kwargs: dict[str, object] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )
        else:
            kwargs["start_new_session"] = True
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.argv,
                cwd=str(self.cwd),
                env=self.env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **kwargs,  # type: ignore[arg-type]
            )
        except OSError as exc:
            self.state = "stopped"
            self.last_exit = Exit(
                None, f"启动不了 dsh：{exc.strerror or type(exc).__name__}", self.clock()
            )
            raise RuntimeError(self.last_exit.why) from None
        self.started_at = self.clock()
        self._reader = asyncio.create_task(self._read(self._proc))
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.start_timeout_s)
        except TimeoutError:
            await self.stop(f"等了 {self.start_timeout_s:.0f} 秒还没启动好，已停止")
            raise RuntimeError(self.last_exit.why if self.last_exit else "dsh 启动超时") from None
        if self.url is None:  # 还没报出地址就退出了
            why = self.last_exit.why if self.last_exit else "dsh 启动失败"
            raise RuntimeError(why)
        return self.url

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace")
            if self.url is None:
                m = _URL.search(line)
                if m and "token=" in m.group(1):
                    self.url = m.group(1)
                    self.state = "running"
                    self._ready.set()
            self.log.append(self._clean(line))
        code = await proc.wait()
        if self.state != "stopping":
            tail = " / ".join(list(self.log)[-3:])
            why = f"dsh 自己退出了（退出码 {code}）" + (f"：{tail}" if tail else "")
            self.last_exit = Exit(code, why, self.clock())
        self.state, self.url, self._proc = "stopped", None, None
        self._ready.set()

    async def stop(self, why: str = "你停止了 dsh") -> bool:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            self.state = "stopped"
            return False
        self.state = "stopping"
        self._terminate(proc)
        try:
            await asyncio.wait_for(proc.wait(), timeout=8)
        except TimeoutError:
            self._kill(proc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
        if self._reader is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._reader, timeout=5)
        self.last_exit = Exit(proc.returncode, why, self.clock())
        self.state, self.url, self._proc = "stopped", None, None
        return True

    @staticmethod
    def _terminate(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            if sys.platform == "win32":
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                os.killpg(proc.pid, signal.SIGTERM)

    @staticmethod
    def _kill(proc: asyncio.subprocess.Process) -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                )
            else:
                os.killpg(proc.pid, signal.SIGKILL)
