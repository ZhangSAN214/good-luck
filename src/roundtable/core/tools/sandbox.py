"""代码运行沙箱。两种后端，接口相同：

- wasm（推荐）：Pyodide（WebAssembly 版 CPython）跑在 Deno 里。Deno 只能读运行时目录和本次的
  工作目录、只能写工作目录的 out/；没有网络、环境变量、子进程权限。代码看到的是内存中的文件系统，
  由 runner.mjs 把 in/、out/ 复制进去、运行后把 out/ 复制出来。
- docker：原生 CPython 容器，--network none、只读根目录、内存与进程数限制、去掉全部权限、
  非 root 用户，只挂载 in/（只读）、out/ 与 main.py。

两种后端都由宿主进程限时（超时直接结束）、监视内存与 out/ 大小、截断输出。
没有可用后端时 python 工具关闭，绝不退回到直接在本机运行。
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import sys
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from roundtable.core.config.schema import PythonTool

RUNNER = Path(__file__).with_name("runner.mjs")
SANDBOX_ENV = "ROUNDTABLE_SANDBOX_DIR"


@dataclass(frozen=True)
class RunOutput:
    stdout: str
    stderr: str
    exit_code: int | None
    duration_s: float
    timed_out: bool = False
    killed: str | None = None  # memory / disk：超出限制被结束

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and not self.killed


def default_runtime_dir(configured: str | None = None) -> Path:
    if configured:
        return Path(configured).expanduser()
    if os.environ.get(SANDBOX_ENV):
        return Path(os.environ[SANDBOX_ENV]).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "roundtable" / "sandbox"


def _minimal_env(extra: dict[str, str]) -> dict[str, str]:
    """子进程的环境变量：只给运行必需的几项，不继承本进程的任何变量（包括 key）。"""
    env = dict(extra)
    if sys.platform == "win32":
        for name in ("SYSTEMROOT", "SystemRoot"):
            if name in os.environ:
                env[name] = os.environ[name]
    return env


class Sandbox(ABC):
    name: str

    def __init__(self, rules: PythonTool) -> None:
        self.rules = rules

    @abstractmethod
    def unavailable_reason(self) -> str | None:
        """不可用时返回原因（界面上显示）。"""

    @abstractmethod
    def command(self, job: Path) -> list[str]:
        """启动命令（静态测试检查其中没有网络等权限）。"""

    def env(self, job: Path) -> dict[str, str]:
        return _minimal_env({})

    async def stop(self, job: Path) -> None:  # noqa: B027 - 可选覆盖
        """结束运行（docker 需要额外结束容器）。"""

    async def run(
        self, job: Path, *, max_out_bytes: int = 2_000_000, max_dir_bytes: int = 200 * 2**20
    ) -> RunOutput:
        """运行 job/main.py。job 下有 in/（只读材料）和 out/（生成的文件）。

        max_out_bytes：stdout / stderr 各保留多少字节；max_dir_bytes：out/ 的大小上限。
        """
        rules = self.rules
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *self.command(job),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(job),
            env=self.env(job),
        )
        killed: list[str] = []

        async def read(stream: asyncio.StreamReader) -> bytes:
            kept = bytearray()
            while chunk := await stream.read(65536):
                if len(kept) < max_out_bytes:  # 超出部分读出后丢弃，避免子进程被管道阻塞
                    kept += chunk[: max_out_bytes - len(kept)]
            return bytes(kept)

        async def watch() -> None:
            memory = (rules.memory_mb + 256) * 2**20  # 加上运行时本身的开销
            while proc.returncode is None:
                await asyncio.sleep(0.2)
                if _tree_rss(proc.pid) > memory:
                    killed.append("memory")
                elif _dir_size(job / "out") > max_dir_bytes:
                    killed.append("disk")
                if killed:
                    await self._kill(proc, job)
                    return

        assert proc.stdout is not None and proc.stderr is not None
        reader = asyncio.gather(read(proc.stdout), read(proc.stderr))
        watcher = asyncio.create_task(watch())
        timed_out = False
        out, err = b"", b""
        try:
            out, err = await asyncio.wait_for(asyncio.shield(reader), timeout=rules.timeout_s)
            await proc.wait()
        except TimeoutError:
            timed_out = True
            await self._kill(proc, job)
            with contextlib.suppress(Exception):
                out, err = await asyncio.wait_for(reader, timeout=5)
        finally:
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher
        failed = timed_out or bool(killed)
        return RunOutput(
            stdout=out.decode("utf-8", errors="replace"),
            stderr=err.decode("utf-8", errors="replace"),
            exit_code=None if failed else proc.returncode,
            duration_s=time.monotonic() - start,
            timed_out=timed_out,
            killed=killed[0] if killed else None,
        )

    async def _kill(self, proc: asyncio.subprocess.Process, job: Path) -> None:
        _kill_tree(proc.pid)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await self.stop(job)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)


def _tree_rss(pid: int) -> int:
    try:
        import psutil
    except ImportError:  # 没有 psutil 时只靠超时与 V8 堆上限
        return 0
    try:
        root = psutil.Process(pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.Error:
        return 0
    total = 0
    for p in procs:
        with contextlib.suppress(psutil.Error):
            total += p.memory_info().rss
    return total


def _kill_tree(pid: int) -> None:
    try:
        import psutil
    except ImportError:
        return
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
    except psutil.Error:
        return
    for p in children:
        with contextlib.suppress(psutil.Error):
            p.kill()


def _dir_size(path: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(dirpath, name)).st_size
    return total


# --- wasm（Deno + Pyodide） --------------------------------------------------------


def runner_digest() -> str:
    return hashlib.sha256(RUNNER.read_bytes()).hexdigest()


class WasmSandbox(Sandbox):
    name = "wasm"

    def __init__(self, rules: PythonTool, runtime_dir: Path | None = None) -> None:
        super().__init__(rules)
        self.runtime = (runtime_dir or default_runtime_dir(rules.runtime_dir)).resolve()

    @property
    def deno(self) -> Path:
        return self.runtime / ("deno.exe" if sys.platform == "win32" else "deno")

    def unavailable_reason(self) -> str | None:
        if not self.deno.is_file():
            return "没有安装代码运行环境（运行 python scripts/setup_sandbox.py）"
        if not (self.runtime / "pyodide" / "pyodide.mjs").is_file():
            return "代码运行环境不完整（重新运行 python scripts/setup_sandbox.py）"
        return None

    def _runner(self) -> Path:
        """运行时目录里的 runner 与仓库中的一致（不一致时覆盖），Deno 只需读运行时目录。"""
        target = self.runtime / "runner.mjs"
        if (
            not target.is_file()
            or hashlib.sha256(target.read_bytes()).hexdigest() != runner_digest()
        ):
            shutil.copyfile(RUNNER, target)
        return target

    def command(self, job: Path) -> list[str]:
        job = job.resolve()
        return [
            str(self.deno),
            "run",
            "--no-prompt",
            "--no-config",
            "--no-lock",
            "--no-remote",
            "--no-npm",
            "--quiet",
            f"--v8-flags=--max-old-space-size={self.rules.memory_mb}",
            f"--allow-read={self.runtime},{job}",
            f"--allow-write={job / 'out'}",
            str(self._runner()),
            str(self.runtime),
            str(job),
        ]

    def env(self, job: Path) -> dict[str, str]:
        return _minimal_env(
            {"DENO_DIR": str(job.resolve() / ".deno"), "DENO_NO_UPDATE_CHECK": "1", "NO_COLOR": "1"}
        )


# --- docker -----------------------------------------------------------------------


class DockerSandbox(Sandbox):
    name = "docker"

    def __init__(self, rules: PythonTool, executable: str | None = None) -> None:
        super().__init__(rules)
        self.executable = executable or shutil.which("docker") or shutil.which("podman")
        self._reason: str | None | bool = False  # False：尚未检查

    def unavailable_reason(self) -> str | None:
        if self._reason is not False:
            return self._reason  # type: ignore[return-value]
        import subprocess

        reason = None
        if not self.executable:
            reason = "没有安装 Docker / Podman"
        else:
            try:
                result = subprocess.run(
                    [self.executable, "image", "inspect", self.rules.docker_image],
                    capture_output=True,
                    timeout=10,
                    env=_minimal_env({"PATH": os.environ.get("PATH", "")}),
                )
                if result.returncode != 0:
                    reason = (
                        f"Docker 不可用或没有镜像 {self.rules.docker_image}（见 sandbox/README）"
                    )
            except (OSError, subprocess.TimeoutExpired):
                reason = "Docker 不可用"
        self._reason = reason
        return reason

    def _name(self, job: Path) -> str:
        return "roundtable-" + hashlib.sha256(str(job.resolve()).encode()).hexdigest()[:16]

    def command(self, job: Path) -> list[str]:
        job = job.resolve()
        m = self.rules.memory_mb
        return [
            str(self.executable or "docker"),
            "run",
            "--rm",
            "--name",
            self._name(job),
            "--network",
            "none",
            "--read-only",
            "--memory",
            f"{m}m",
            "--memory-swap",
            f"{m}m",
            "--pids-limit",
            "64",
            "--cpus",
            "1",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65534:65534",
            "--tmpfs",
            "/tmp:size=64m",
            "-e",
            "MPLBACKEND=Agg",
            "-e",
            "HOME=/tmp",
            "-v",
            f"{job / 'in'}:/work/in:ro",
            "-v",
            f"{job / 'out'}:/work/out:rw",
            "-v",
            f"{job / 'main.py'}:/work/main.py:ro",
            "-w",
            "/work",
            self.rules.docker_image,
            "python",
            "/work/main.py",
        ]

    def env(self, job: Path) -> dict[str, str]:
        keep = {k: os.environ[k] for k in ("PATH", "DOCKER_HOST") if k in os.environ}
        return _minimal_env(keep)

    async def stop(self, job: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            str(self.executable),
            "kill",
            self._name(job),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=self.env(job),
        )
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=10)


def pick_sandbox(rules: PythonTool) -> tuple[Sandbox | None, str | None]:
    """按配置选择后端。返回 (沙箱, 不可用原因)。"""
    if rules.backend == "off":
        return None, "配置中关闭了代码运行"
    candidates: list[Sandbox] = []
    if rules.backend in ("auto", "wasm"):
        candidates.append(WasmSandbox(rules))
    if rules.backend in ("auto", "docker"):
        candidates.append(DockerSandbox(rules))
    reasons = []
    for sandbox in candidates:
        reason = sandbox.unavailable_reason()
        if reason is None:
            return sandbox, None
        reasons.append(reason)
    return None, "；".join(reasons)


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]
