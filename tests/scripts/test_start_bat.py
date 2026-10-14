"""Windows 一键启动脚本：进入项目目录、激活 .venv、更新、安装、启动服务并打开浏览器。"""

from __future__ import annotations

from pathlib import Path

BAT = Path(__file__).resolve().parents[2] / "start.bat"


def test_start_bat_steps_in_order():
    text = BAT.read_text(encoding="ascii")  # 纯 ASCII：不受控制台代码页影响
    steps = [
        'cd /d "%~dp0"',
        r".venv\Scripts\activate.bat",
        "git pull",
        'pip install -e ".[dev]"',
        "http://127.0.0.1:8000",
        "uvicorn roundtable.api.app:app",
    ]
    positions = [text.index(s) for s in steps]
    assert positions == sorted(positions)


def test_start_bat_has_no_secrets():
    text = BAT.read_text(encoding="ascii")
    assert "API_KEY=" not in text and "sk-" not in text


def test_start_bat_installs_sandbox_before_server():
    text = BAT.read_text(encoding="ascii")
    assert text.index("setup_sandbox.py --check") < text.index("uvicorn")
