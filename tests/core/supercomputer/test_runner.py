from __future__ import annotations

import sys
import textwrap

from roundtable.core.supercomputer.runner import hit_limit, run

SCRIPT = textwrap.dedent(
    """
    import os, sys, time
    data = sys.stdin.read()
    if os.environ.get("MODE") == "sleep":
        time.sleep(30)
    if os.environ.get("MODE") == "limit":
        print("Error: usage limit reached, try later", file=sys.stderr)
        sys.exit(1)
    print("cwd=" + os.path.basename(os.getcwd()))
    print("got=" + data.strip() + " key=S3CRET")
    """
)


def argv(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text(SCRIPT, encoding="utf-8")
    return [sys.executable, str(script)]


async def test_stdin_cwd_and_redaction(tmp_path):
    r = await run(
        argv(tmp_path),
        stdin_text="做个小活",
        cwd=tmp_path / "work",
        env={"PATH": ""},
        timeout_s=20,
        secrets=["S3CRET"],
    )
    assert r.ok and "cwd=work" in r.output and "got=做个小活" in r.output
    assert "S3CRET" not in r.output and "key=***" in r.output


async def test_limit_message_on_stderr(tmp_path):
    r = await run(argv(tmp_path), stdin_text="x", cwd=tmp_path, env={"MODE": "limit"}, timeout_s=20)
    assert not r.ok and r.code == 1
    assert hit_limit(r.output + r.diagnostics, ["usage limit"])
    assert not hit_limit("all good", ["usage limit"])


async def test_timeout_kills(tmp_path):
    r = await run(argv(tmp_path), stdin_text="x", cwd=tmp_path, env={"MODE": "sleep"}, timeout_s=1)
    assert r.timed_out and not r.ok


async def test_missing_executable(tmp_path):
    r = await run([str(tmp_path / "nope")], stdin_text="", cwd=tmp_path, env={}, timeout_s=5)
    assert r.error and "启动不了" in r.error and not r.ok


STREAM = textwrap.dedent(
    """
    import json, sys, time
    sys.stdin.read()
    print(json.dumps({"type": "session", "sessionId": "s"}), flush=True)
    for i in range(int(sys.argv[1])):
        print(json.dumps({"type": "tool_call", "tool": "bash"}), flush=True)
    print(json.dumps({"type": "final", "text": "完成 S3CRET"}), flush=True)
    if len(sys.argv) > 2:
        time.sleep(30)  # 停不下来就会超时
    """
)


class Counter:
    def __init__(self, cap):
        self.cap, self.n = cap, 0

    def feed(self, line):
        if '"tool_call"' in line:
            self.n += 1
            return "够了" if self.n > self.cap else None
        return None


async def test_watch_reads_lines_and_can_stop(tmp_path):
    script = tmp_path / "stream.py"
    script.write_text(STREAM, encoding="utf-8")
    kw = dict(stdin_text="x", cwd=tmp_path / "w", env={"PATH": ""}, secrets=["S3CRET"])
    watch = Counter(100)
    r = await run([sys.executable, str(script), "3"], timeout_s=20, watch=watch, **kw)
    assert r.ok and watch.n == 3 and '"final"' in r.output and "S3CRET" not in r.output
    watch = Counter(2)
    r = await run([sys.executable, str(script), "50", "hang"], timeout_s=20, watch=watch, **kw)
    assert r.code == 0 and not r.timed_out and watch.n == 3  # 第 3 步就结束了进程
