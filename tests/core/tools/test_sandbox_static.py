"""沙箱启动参数的静态检查（始终运行，不需要安装沙箱）。"""

from __future__ import annotations

from pathlib import Path

from roundtable.core.config import load_config
from roundtable.core.tools import DockerSandbox, WasmSandbox, pick_sandbox

RULES = load_config().roundtable.tools.python
ROOT = Path(__file__).resolve().parents[3]
FAKE_KEY = "sk-or-v1-" + "f" * 40


def test_wasm_permissions_only_runtime_and_job(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    runtime = tmp_path / "rt"
    runtime.mkdir()
    job = tmp_path / "job"
    sandbox = WasmSandbox(RULES, runtime)
    cmd = sandbox.command(job)
    flags = [c for c in cmd if c.startswith("--")]
    assert not any(
        f.startswith(
            (
                "--allow-net",
                "--allow-env",
                "--allow-run",
                "--allow-ffi",
                "--allow-sys",
                "--allow-all",
                "-A",
            )
        )
        for f in flags
    )
    reads = next(f for f in flags if f.startswith("--allow-read="))
    assert sorted(reads.split("=", 1)[1].split(",")) == sorted(
        [str(runtime.resolve()), str(job.resolve())]
    )
    writes = next(f for f in flags if f.startswith("--allow-write="))
    assert writes == f"--allow-write={job.resolve() / 'out'}"
    assert "--no-remote" in flags and "--no-npm" in flags
    assert str(ROOT) not in " ".join(cmd)  # 不授予项目目录
    env = sandbox.env(job)
    assert FAKE_KEY not in str(env) and "OPENROUTER_API_KEY" not in env
    assert set(env) <= {"DENO_DIR", "DENO_NO_UPDATE_CHECK", "NO_COLOR", "SYSTEMROOT", "SystemRoot"}


def test_docker_isolation_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    sandbox = DockerSandbox(RULES, executable="docker")
    cmd = sandbox.command(tmp_path)
    joined = " ".join(cmd)
    for flag in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "no-new-privileges",
        "--pids-limit",
        "--memory",
        "--user 65534:65534",
    ):
        assert flag in joined
    mounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert [m.split(":")[0] for m in mounts] == [
        str(tmp_path.resolve() / "in"),
        str(tmp_path.resolve() / "out"),
        str(tmp_path.resolve() / "main.py"),
    ]
    assert mounts[0].endswith(":ro") and mounts[2].endswith(":ro")
    assert FAKE_KEY not in str(sandbox.env(tmp_path)) and "-e" in cmd
    assert not any(FAKE_KEY in c or "API_KEY" in c for c in cmd)


def test_no_backend_means_no_python(tmp_path, monkeypatch):
    off = RULES.model_copy(update={"backend": "off"})
    assert pick_sandbox(off) == (None, "配置中关闭了代码运行")
    missing = RULES.model_copy(update={"backend": "wasm", "runtime_dir": str(tmp_path)})
    sandbox, reason = pick_sandbox(missing)
    assert sandbox is None and "setup_sandbox" in reason
