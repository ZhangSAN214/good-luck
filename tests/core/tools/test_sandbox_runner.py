"""沙箱宿主逻辑（限时、截断、内存 / 磁盘监视、后端选择），始终运行，不需要安装沙箱。

真实后端（Deno / Docker）在 CI 上通常没有，越权测试会跳过；这里用一个只在测试里存在的
后端，把 ``Sandbox.run`` 的命令换成本机 Python 子进程，单独检查宿主进程负责的那部分：
超时直接结束、输出截断但不阻塞、超内存 / out/ 超大时结束、环境变量不继承。
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path

import pytest

import roundtable.core.tools.sandbox as sb
from roundtable.core.config import load_config
from roundtable.core.tools import DockerSandbox, WasmSandbox, pick_sandbox

RULES = load_config().roundtable.tools.python
FAKE_KEY = "sk-or-v1-" + "a" * 40


class LocalSandbox(sb.Sandbox):
    """测试专用：直接用当前解释器跑 job/main.py（生产代码里不存在这种后端）。"""

    name = "local-test"

    def __init__(self, rules, *, env_extra=None):
        super().__init__(rules)
        self.stopped: list[Path] = []
        self._env_extra = env_extra or {}

    def unavailable_reason(self):
        return None

    def command(self, job: Path) -> list[str]:
        return [sys.executable, "-I", str(job / "main.py")]

    def env(self, job: Path) -> dict[str, str]:
        return sb._minimal_env(dict(self._env_extra))

    async def stop(self, job: Path) -> None:
        self.stopped.append(job)


def make_job(tmp_path: Path, code: str) -> Path:
    job = tmp_path / "job"
    (job / "in").mkdir(parents=True)
    (job / "out").mkdir()
    (job / "main.py").write_text(code, encoding="utf-8")
    return job


def run(sandbox, job, **kw):
    return asyncio.run(sandbox.run(job, **kw))


# --- RunOutput ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kw", "ok"),
    [
        ({"exit_code": 0}, True),
        ({"exit_code": 1}, False),
        ({"exit_code": None}, False),
        ({"exit_code": 0, "timed_out": True}, False),
        ({"exit_code": 0, "killed": "memory"}, False),
    ],
)
def test_run_output_ok_requires_clean_exit(kw, ok):
    out = sb.RunOutput(stdout="", stderr="", duration_s=0.1, **kw)
    assert out.ok is ok


# --- Sandbox.run -------------------------------------------------------------------


def test_run_collects_output_and_exit_code(tmp_path):
    job = make_job(
        tmp_path,
        "import sys\nprint('你好')\nprint('err', file=sys.stderr)\n"
        "open('out/a.txt', 'w').write('x')\nsys.exit(3)\n",
    )
    result = run(LocalSandbox(RULES), job)
    assert result.stdout.strip() == "你好"
    assert result.stderr.strip() == "err"
    assert result.exit_code == 3 and not result.ok
    assert not result.timed_out and result.killed is None
    assert (job / "out" / "a.txt").read_text() == "x"


def test_run_starts_in_job_dir_without_stdin(tmp_path):
    job = make_job(
        tmp_path,
        "import os, sys\nprint(os.getcwd())\nprint(repr(sys.stdin.read()))\n",
    )
    result = run(LocalSandbox(RULES), job)
    assert result.ok, result.stderr
    cwd, stdin = result.stdout.splitlines()
    assert Path(cwd).resolve() == job.resolve()
    assert stdin == "''"  # stdin 是 /dev/null，不会卡住等输入


def test_run_does_not_leak_parent_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setenv("ROUNDTABLE_LEAK_PROBE", "1")
    job = make_job(tmp_path, "import os\nprint(sorted(os.environ))\n")
    result = run(LocalSandbox(RULES, env_extra={"ONLY": "this"}), job)
    assert result.ok, result.stderr
    assert FAKE_KEY not in result.stdout
    assert "OPENROUTER_API_KEY" not in result.stdout
    assert "ROUNDTABLE_LEAK_PROBE" not in result.stdout
    assert "ONLY" in result.stdout


def test_run_truncates_each_stream_without_blocking(tmp_path):
    # 远超管道缓冲的输出：宿主必须一直读（丢弃超出部分），否则子进程会卡死直到超时
    job = make_job(
        tmp_path,
        "import sys\nsys.stdout.write('o' * 3_000_000)\nsys.stderr.write('e' * 3_000_000)\n",
    )
    rules = RULES.model_copy(update={"timeout_s": 30})
    result = run(LocalSandbox(rules), job, max_out_bytes=1000)
    assert result.ok and not result.timed_out
    assert result.stdout == "o" * 1000
    assert result.stderr == "e" * 1000


def test_run_decodes_invalid_utf8_with_replacement(tmp_path):
    job = make_job(tmp_path, "import sys\nsys.stdout.buffer.write(b'ok\\xff\\xfe')\n")
    result = run(LocalSandbox(RULES), job)
    assert result.ok
    assert result.stdout.startswith("ok") and "�" in result.stdout


def test_run_timeout_kills_process_and_keeps_partial_output(tmp_path):
    job = make_job(
        tmp_path,
        "import sys, time\nprint('started', flush=True)\ntime.sleep(60)\nprint('never')\n",
    )
    sandbox = LocalSandbox(RULES.model_copy(update={"timeout_s": 1.0}))
    result = run(sandbox, job)
    assert result.timed_out and not result.ok
    assert result.exit_code is None  # 超时不报子进程的退出码
    assert "started" in result.stdout and "never" not in result.stdout
    assert result.duration_s < 20
    assert sandbox.stopped == [job]  # 后端自己的结束钩子（docker kill）也被调用


def test_run_kills_when_out_dir_exceeds_limit(tmp_path):
    job = make_job(
        tmp_path,
        "import time\nopen('out/big.bin', 'wb').write(b'0' * 200_000)\ntime.sleep(30)\n",
    )
    sandbox = LocalSandbox(RULES.model_copy(update={"timeout_s": 20}))
    result = run(sandbox, job, max_dir_bytes=10_000)
    assert result.killed == "disk"
    assert not result.timed_out and result.exit_code is None and not result.ok
    assert result.duration_s < 15
    assert sandbox.stopped == [job]


def test_run_kills_when_memory_exceeds_limit(tmp_path, monkeypatch):
    # 进程树内存由 psutil 读；这里直接让它报超限，检查宿主会结束并标记 memory
    monkeypatch.setattr(sb, "_tree_rss", lambda pid: 10**15)
    job = make_job(tmp_path, "import time\ntime.sleep(30)\n")
    sandbox = LocalSandbox(RULES.model_copy(update={"timeout_s": 20}))
    result = run(sandbox, job)
    assert result.killed == "memory"
    assert not result.timed_out and result.exit_code is None
    assert result.duration_s < 15


def test_memory_check_takes_precedence_over_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(sb, "_tree_rss", lambda pid: 10**15)
    monkeypatch.setattr(sb, "_dir_size", lambda path: 10**15)
    job = make_job(tmp_path, "import time\ntime.sleep(30)\n")
    result = run(LocalSandbox(RULES.model_copy(update={"timeout_s": 20})), job)
    assert result.killed == "memory"


# --- 辅助函数 ------------------------------------------------------------------------


def test_dir_size_counts_nested_files_and_ignores_symlink_targets(tmp_path):
    out = tmp_path / "out"
    (out / "sub").mkdir(parents=True)
    (out / "a").write_bytes(b"x" * 10)
    (out / "sub" / "b").write_bytes(b"y" * 5)
    big = tmp_path / "outside.bin"
    big.write_bytes(b"z" * 100_000)
    try:
        (out / "link").symlink_to(big)
    except OSError:  # 某些系统不允许建符号链接
        pytest.skip("不能建符号链接")
    size = sb._dir_size(out)
    # 符号链接按链接本身（lstat）算，不跟到外面的大文件
    assert 15 <= size < 15 + 4096


def test_dir_size_of_missing_dir_is_zero(tmp_path):
    assert sb._dir_size(tmp_path / "nope") == 0


def test_tree_rss_and_kill_tree_tolerate_dead_pid():
    dead = 2**22 + 12345  # 不存在的进程号
    assert sb._tree_rss(dead) == 0
    sb._kill_tree(dead)  # 不抛异常


def test_tree_rss_of_self_is_positive():
    assert sb._tree_rss(os.getpid()) > 0


def test_minimal_env_does_not_inherit(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    env = sb._minimal_env({"A": "1"})
    assert env["A"] == "1"
    assert "ANTHROPIC_API_KEY" not in env and "PATH" not in env


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_default_runtime_dir_precedence(tmp_path, monkeypatch, platform):
    monkeypatch.setattr(sb.sys, "platform", platform)
    monkeypatch.setenv(sb.SANDBOX_ENV, str(tmp_path / "env"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    # 配置优先于环境变量
    assert sb.default_runtime_dir(str(tmp_path / "cfg")) == tmp_path / "cfg"
    assert sb.default_runtime_dir() == tmp_path / "env"
    monkeypatch.delenv(sb.SANDBOX_ENV)
    assert sb.default_runtime_dir() == tmp_path / "xdg" / "roundtable" / "sandbox"
    monkeypatch.delenv("XDG_CACHE_HOME")
    assert sb.default_runtime_dir() == Path.home() / ".cache" / "roundtable" / "sandbox"


def test_default_runtime_dir_on_windows_uses_localappdata(tmp_path, monkeypatch):
    monkeypatch.setattr(sb.sys, "platform", "win32")
    monkeypatch.delenv(sb.SANDBOX_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert sb.default_runtime_dir() == tmp_path / "local" / "roundtable" / "sandbox"


def test_default_runtime_dir_expands_user(monkeypatch):
    monkeypatch.delenv(sb.SANDBOX_ENV, raising=False)
    assert sb.default_runtime_dir("~/rt") == Path.home() / "rt"


# --- wasm 后端 ----------------------------------------------------------------------


def test_wasm_unavailable_reasons(tmp_path):
    sandbox = WasmSandbox(RULES, tmp_path)
    assert "没有安装" in sandbox.unavailable_reason()
    sandbox.deno.write_bytes(b"")
    assert "不完整" in sandbox.unavailable_reason()
    (tmp_path / "pyodide").mkdir()
    (tmp_path / "pyodide" / "pyodide.mjs").write_text("")
    assert sandbox.unavailable_reason() is None


def test_wasm_runner_is_copied_and_refreshed_when_tampered(tmp_path):
    sandbox = WasmSandbox(RULES, tmp_path)
    target = sandbox._runner()
    assert target == tmp_path.resolve() / "runner.mjs"
    assert target.read_bytes() == sb.RUNNER.read_bytes()
    # 运行时目录里的 runner 被改过：下次启动前覆盖回仓库版本
    target.write_text("// tampered")
    sandbox._runner()
    assert target.read_bytes() == sb.RUNNER.read_bytes()
    assert sb.runner_digest() == __import__("hashlib").sha256(target.read_bytes()).hexdigest()


def test_wasm_env_points_deno_dir_into_job(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    env = WasmSandbox(RULES, tmp_path / "rt").env(tmp_path / "job")
    assert env["DENO_DIR"] == str((tmp_path / "job").resolve() / ".deno")
    assert env["DENO_NO_UPDATE_CHECK"] == "1"
    assert FAKE_KEY not in str(env)


def test_wasm_memory_flag_follows_rules(tmp_path):
    rules = RULES.model_copy(update={"memory_mb": 321})
    cmd = WasmSandbox(rules, tmp_path).command(tmp_path / "job")
    assert "--v8-flags=--max-old-space-size=321" in cmd


# --- docker 后端 --------------------------------------------------------------------


def _fake_docker(tmp_path: Path, exit_code: int) -> Path:
    exe = tmp_path / "fake-docker"
    exe.write_text(f'#!/bin/sh\necho "$@" >> {tmp_path / "calls.log"}\nexit {exit_code}\n')
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return exe


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="假 docker 用 sh 脚本")


def test_docker_without_executable(monkeypatch):
    monkeypatch.setattr(sb.shutil, "which", lambda name: None)
    sandbox = DockerSandbox(RULES)
    assert sandbox.executable is None
    assert sandbox.unavailable_reason() == "没有安装 Docker / Podman"


@posix_only
def test_docker_missing_image_reason_is_cached(tmp_path):
    exe = _fake_docker(tmp_path, exit_code=1)
    sandbox = DockerSandbox(RULES, executable=str(exe))
    reason = sandbox.unavailable_reason()
    assert RULES.docker_image in reason
    assert sandbox.unavailable_reason() == reason
    calls = (tmp_path / "calls.log").read_text().splitlines()
    assert calls == [f"image inspect {RULES.docker_image}"]  # 只检查一次


@posix_only
def test_docker_available_when_image_present(tmp_path):
    sandbox = DockerSandbox(RULES, executable=str(_fake_docker(tmp_path, exit_code=0)))
    assert sandbox.unavailable_reason() is None
    assert sandbox.unavailable_reason() is None
    assert len((tmp_path / "calls.log").read_text().splitlines()) == 1


def test_docker_executable_that_cannot_start(tmp_path):
    sandbox = DockerSandbox(RULES, executable=str(tmp_path / "missing-binary"))
    assert sandbox.unavailable_reason() == "Docker 不可用"


def test_docker_container_name_is_stable_per_job(tmp_path):
    sandbox = DockerSandbox(RULES, executable="docker")
    a, b = tmp_path / "a", tmp_path / "b"
    assert sandbox._name(a) == sandbox._name(a)
    assert sandbox._name(a) != sandbox._name(b)
    assert sandbox._name(a).startswith("roundtable-") and len(sandbox._name(a)) == 27
    assert sandbox._name(a) in sandbox.command(a)


def test_docker_env_keeps_only_path_and_host(monkeypatch, tmp_path):
    monkeypatch.setenv("DOCKER_HOST", "unix:///x.sock")
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_KEY)
    env = DockerSandbox(RULES, executable="docker").env(tmp_path)
    assert set(env) <= {"PATH", "DOCKER_HOST", "SYSTEMROOT", "SystemRoot"}
    assert env["DOCKER_HOST"] == "unix:///x.sock"


@posix_only
def test_docker_stop_kills_named_container(tmp_path):
    exe = _fake_docker(tmp_path, exit_code=0)
    sandbox = DockerSandbox(RULES, executable=str(exe))
    job = tmp_path / "job"
    asyncio.run(sandbox.stop(job))
    assert (tmp_path / "calls.log").read_text().strip() == f"kill {sandbox._name(job)}"


# --- 选择后端 ----------------------------------------------------------------------


def test_pick_sandbox_auto_falls_back_to_docker(tmp_path, monkeypatch):
    rules = RULES.model_copy(update={"backend": "auto", "runtime_dir": str(tmp_path)})
    monkeypatch.setattr(DockerSandbox, "unavailable_reason", lambda self: None)
    sandbox, reason = pick_sandbox(rules)
    assert isinstance(sandbox, DockerSandbox) and reason is None


def test_pick_sandbox_auto_prefers_wasm(tmp_path, monkeypatch):
    rules = RULES.model_copy(update={"backend": "auto", "runtime_dir": str(tmp_path)})
    monkeypatch.setattr(WasmSandbox, "unavailable_reason", lambda self: None)
    monkeypatch.setattr(
        DockerSandbox, "unavailable_reason", lambda self: pytest.fail("不该检查 docker")
    )
    sandbox, reason = pick_sandbox(rules)
    assert isinstance(sandbox, WasmSandbox) and reason is None


def test_pick_sandbox_auto_reports_every_reason(tmp_path, monkeypatch):
    rules = RULES.model_copy(update={"backend": "auto", "runtime_dir": str(tmp_path)})
    monkeypatch.setattr(DockerSandbox, "unavailable_reason", lambda self: "无 docker")
    sandbox, reason = pick_sandbox(rules)
    assert sandbox is None
    assert "setup_sandbox" in reason and reason.endswith("；无 docker")


def test_pick_sandbox_docker_only_never_tries_wasm(tmp_path, monkeypatch):
    rules = RULES.model_copy(update={"backend": "docker"})
    monkeypatch.setattr(
        WasmSandbox, "unavailable_reason", lambda self: pytest.fail("不该检查 wasm")
    )
    monkeypatch.setattr(DockerSandbox, "unavailable_reason", lambda self: "无 docker")
    assert pick_sandbox(rules) == (None, "无 docker")
