"""在电脑上跑一个编程助手的命令行：任务正文从标准输入给，读完输出；超时连同子进程一起结束。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

OUTPUT_LIMIT = 200_000  # 最多保留多少字的输出
DIAG_LIMIT = 4000  # 标准错误只留最后这么多字
LINE_LIMIT = 16 * 1024 * 1024  # 一行最长多少字节（dsh 事件流最后的 final 一行不限长）


@dataclass(frozen=True)
class RunResult:
    code: int | None
    output: str  # 标准输出（助手的回答）
    diagnostics: str = ""  # 标准错误的最后一段（进度、报错）
    timed_out: bool = False
    error: str | None = None  # 启动不了时的原因

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out and self.error is None


class LineWatch(Protocol):
    """边跑边看标准输出的每一行（如 dsh 的事件流）；返回原因就结束进程。"""

    def feed(self, line: str) -> str | None: ...


def hit_limit(text: str, patterns: Sequence[str]) -> bool:
    low = text.lower()
    return any(p.lower() in low for p in patterns if p)


def _clean(text: str, secrets: Sequence[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return re.sub(r"(token=)[^\s&]+", r"\1***", text)


async def run(
    argv: Sequence[str],
    *,
    stdin_text: str,
    cwd: Path,
    env: dict[str, str],
    timeout_s: float,
    secrets: Sequence[str] = (),
    watch: LineWatch | None = None,
) -> RunResult:
    cwd.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, object] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
            **kwargs,  # type: ignore[arg-type]
        )
    except OSError as exc:
        return RunResult(None, "", error=f"启动不了：{exc.strerror or type(exc).__name__}")
    try:
        if watch is None:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin_text.encode("utf-8")), timeout=timeout_s
            )
        else:
            out, err, stopped = await asyncio.wait_for(
                _watched(proc, stdin_text, watch), timeout=timeout_s
            )
            if stopped:
                text = out.decode("utf-8", errors="replace")[-OUTPUT_LIMIT:]
                diag = err.decode("utf-8", errors="replace")[-DIAG_LIMIT:]
                return RunResult(0, _clean(text, secrets), _clean(diag, secrets))
    except TimeoutError:
        _kill(proc)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=5)
        return RunResult(proc.returncode, "", timed_out=True)
    except asyncio.CancelledError:
        _kill(proc)
        raise
    text = out.decode("utf-8", errors="replace")[-OUTPUT_LIMIT:]
    diag = err.decode("utf-8", errors="replace")[-DIAG_LIMIT:]
    return RunResult(proc.returncode, _clean(text, secrets), _clean(diag, secrets))


async def _watched(
    proc: asyncio.subprocess.Process, stdin_text: str, watch: LineWatch
) -> tuple[bytes, bytes, bool]:
    """一行一行读标准输出交给 watch；它喊停就结束进程。返回（输出, 标准错误, 是否喊停）。"""
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    proc.stdin.write(stdin_text.encode("utf-8"))
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        await proc.stdin.drain()
    proc.stdin.close()
    err_task = asyncio.ensure_future(proc.stderr.read())
    lines: list[bytes] = []
    stopped = False
    try:
        while line := await proc.stdout.readline():
            lines.append(line)
            if watch.feed(line.decode("utf-8", errors="replace")):
                stopped = True
                _kill(proc)
                break
        if stopped:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
            err_task.cancel()
            return b"".join(lines), b"", True
        err = await err_task
        await proc.wait()
        return b"".join(lines), err, False
    except BaseException:
        err_task.cancel()
        raise


def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
