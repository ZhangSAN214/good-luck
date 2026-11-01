"""dsh 工作台测试共用：假的 dsh 进程（一个 Python 脚本），不装 Node、不联网。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

FAKE_DSH = """
import os, sys, time
print("dsh: booting", flush=True)
if os.environ.get("FAKE_DSH_FAIL"):
    print("dsh: boom", flush=True)
    sys.exit(3)
print("dsh web: http://127.0.0.1:3999/?token=SECRETTOKEN123", flush=True)
print("key is " + os.environ.get("FAKE_KEY_ENV_VALUE_ECHO", ""), flush=True)
time.sleep(120)
"""


@pytest.fixture
def fake_dsh(tmp_path: Path) -> list[str]:
    script = tmp_path / "fake_dsh.py"
    script.write_text(FAKE_DSH, encoding="utf-8")
    return [sys.executable, "-u", str(script)]
