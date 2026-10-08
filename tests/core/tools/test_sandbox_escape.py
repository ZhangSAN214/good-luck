"""越权测试：在真实沙箱里运行恶意代码，全部必须失败或被拦下。

本机没有对应后端时自动跳过
（wasm：python scripts/setup_sandbox.py；docker：镜像 roundtable-sandbox:1，见 sandbox/README.md）。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from roundtable.core.config import load_config
from roundtable.core.tools import DockerSandbox, WasmSandbox

ROOT = Path(__file__).resolve().parents[3]
RULES = load_config().roundtable.tools.python.model_copy(update={"timeout_s": 40, "memory_mb": 512})
SECRET = "sk-or-v1-" + "e" * 40


def backends():
    out = []
    for cls in (WasmSandbox, DockerSandbox):
        sandbox = cls(RULES)
        reason = sandbox.unavailable_reason()
        out.append(
            pytest.param(
                sandbox,
                id=cls.name,
                marks=pytest.mark.skipif(reason is not None, reason=reason or ""),
            )
        )
    return out


@pytest.fixture(params=backends())
def sandbox(request, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)  # 子进程不应看到本进程的环境变量
    return request.param


def run(sandbox, code: str, files: dict[str, bytes] | None = None):
    job = Path(tempfile.mkdtemp(prefix="roundtable-test-"))
    try:
        (job / "in").mkdir()
        (job / "out").mkdir()
        for name, data in (files or {}).items():
            (job / "in" / name).write_bytes(data)
        (job / "main.py").write_text(code, encoding="utf-8")
        result = asyncio.run(sandbox.run(job, max_out_bytes=20000, max_dir_bytes=5 * 2**20))
        out = {p.relative_to(job / "out").as_posix(): p for p in (job / "out").rglob("*")}
        return result, {
            k: (v.is_symlink(), v.read_bytes() if v.is_file() and not v.is_symlink() else b"")
            for k, v in out.items()
        }
    finally:
        shutil.rmtree(job, ignore_errors=True)


PROBE = """
import json, os, sys
results = {}
def probe(name, fn):
    try:
        value = fn()
        results[name] = "OK:" + str(value)[:200]
    except BaseException as e:
        results[name] = "DENIED:" + type(e).__name__
"""


def test_basic_run_and_outputs(sandbox):
    result, out = run(
        sandbox,
        "print(open('in/a.txt').read()); open('out/b.txt','w').write('hi')",
        {"a.txt": b"material"},
    )
    assert result.ok and "material" in result.stdout
    assert out["b.txt"] == (False, b"hi")


def test_cannot_read_project_or_system_files(sandbox):
    targets = {
        "env_abs": str(ROOT / ".env.example"),
        "src": str(ROOT / "CLAUDE.md"),
        "passwd": "/etc/passwd",
        "win": "C:\\\\Windows\\\\win.ini",
        "rel": "../../../../" + str(ROOT).lstrip("/") + "/.env.example",
    }
    code = (
        PROBE
        + "".join(f"probe({k!r}, lambda: open({v!r}).read())\n" for k, v in targets.items())
        + f"""
probe("home", lambda: os.listdir({str(Path.home())!r}))
probe("env", lambda: os.environ.get("OPENROUTER_API_KEY"))
probe("environ_secret", lambda: [k for k, v in os.environ.items() if "sk-or" in v])
print(json.dumps(results))
"""
    )
    result, _ = run(sandbox, code)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    for key in targets:
        assert data[key].startswith("DENIED"), (key, data[key])
    assert data["env"] in ("OK:None", "DENIED:KeyError")
    host_home = {n for n in os.listdir(Path.home()) if len(n) > 3}
    assert data["home"].startswith("DENIED") or not any(n in data["home"] for n in host_home)
    assert data["environ_secret"] == "OK:[]"
    assert SECRET not in result.stdout + result.stderr
    assert "Copy this file" not in result.stdout  # .env.example 的内容


def test_cannot_use_network_or_processes(sandbox):
    code = (
        PROBE
        + """
import socket, subprocess
def connect(host, port):
    s = socket.create_connection((host, port), timeout=3); s.close(); return "connected"
probe("local", lambda: connect("127.0.0.1", 8000))
probe("remote", lambda: connect("1.1.1.1", 443))
import urllib.request
probe("urllib", lambda: urllib.request.urlopen("https://example.com", timeout=3).status)
probe("subprocess", lambda: subprocess.run(["ls", "/"], capture_output=True).stdout)
probe("system", lambda: os.system("echo pwned") or "ran")
try:
    import js
    probe("js_env", lambda: js.Deno.env.get("OPENROUTER_API_KEY"))
    probe("js_read", lambda: js.Deno.readTextFileSync("%s"))
    probe("js_run", lambda: js.Deno.Command.new("ls").outputSync())
    probe("js_fetch", lambda: js.fetch("https://example.com"))
    probe("js_write", lambda: js.Deno.writeTextFileSync("/tmp/roundtable-escape.txt", "x"))
except ImportError:
    pass
print(json.dumps(results))
"""
        % (ROOT / ".env.example")
    )
    result, _ = run(sandbox, code)
    data = json.loads(result.stdout.strip().splitlines()[-1])
    for key, value in data.items():
        if key == "js_fetch" and value.startswith(("OK:<Promise", "OK:<PyodideFuture")):
            continue  # fetch 返回 Promise；真正的请求在下面单独检查
        if key == "system":
            assert value != "OK:ran" and "pwned" not in result.stdout, value
            continue
        assert value.startswith("DENIED"), (key, value)
    assert not Path("/tmp/roundtable-escape.txt").exists()


def test_async_fetch_denied(sandbox):
    if sandbox.name != "wasm":
        pytest.skip("只适用于 wasm 后端")
    code = """
import js
try:
    r = await js.fetch("https://example.com")
    print("OK", r.status)
except Exception as e:
    print("DENIED", type(e).__name__)
"""
    result, _ = run(sandbox, code)
    assert "DENIED" in result.stdout


def test_symlink_in_out_is_not_exported(sandbox):
    code = f"""
import os
try:
    os.symlink({str(ROOT / ".env.example")!r}, "out/env.txt")
except Exception as e:
    print("symlink failed", type(e).__name__)
try:
    import js
    js.Deno.symlinkSync({str(ROOT / ".env.example")!r}, "{{job}}/out/js.txt")
except Exception as e:
    print("js symlink failed")
"""
    result, out = run(sandbox, code)
    for name, (is_link, data) in out.items():
        assert not is_link, (
            name
        )  # 沙箱里建的链接不会出现在宿主的 out/（收集时也会拒绝，见 test_files）
        assert b"OPENROUTER" not in data


def test_timeout_memory_and_output_limits(sandbox):
    result, _ = run(sandbox, "while True:\n    pass\n")
    assert result.timed_out and not result.ok
    rules = RULES.model_copy(update={"timeout_s": 60, "memory_mb": 256})
    hog = type(sandbox)(rules)
    result, _ = run(hog, "x = []\nwhile True:\n    x.append(bytearray(50 * 2**20))\n")
    assert not result.ok and (result.killed == "memory" or "MemoryError" in result.stderr), result
    result, _ = run(sandbox, "print('x' * 10**7)")
    assert len(result.stdout) <= 20000
    result, _ = run(
        sandbox, "open('out/big.bin', 'wb').write(b'0' * 50 * 2**20)\nimport time; time.sleep(5)"
    )
    assert not result.ok
