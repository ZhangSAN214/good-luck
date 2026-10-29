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

OUTPUT_LIMIT = 200_000  # 最多保留多少字的输出
DIAG_LIMIT = 4000  # 标准错误只留最后这么多字


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
            **kwargs,  # type: ignore[arg-type]
        )
    except OSError as exc:
        return RunResult(None, "", error=f"启动不了：{exc.strerror or type(exc).__name__}")
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin_text.encode("utf-8")), timeout=timeout_s
        )
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


def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
