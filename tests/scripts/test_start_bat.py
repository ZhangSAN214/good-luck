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


def test_start_bat_never_waits_for_git_credentials():
    # 隐藏的凭据窗口会让启动看起来卡住：拉取失败就用本地代码启动
    text = BAT.read_text(encoding="ascii")
    assert text.index("GIT_TERMINAL_PROMPT=0") < text.index("git pull")
    assert "GCM_INTERACTIVE=never" in text


def test_start_bat_replaces_an_old_server_and_only_stops_python():
    # 8000 端口上已有圆桌服务（旧窗口或后台启动的）时先停掉再启动；别的程序不动
    for name in ("start.bat", "update_and_start.bat", "stop.bat"):
        text = (BAT.parent / name).read_text(encoding="ascii")
        assert ":8000 .*LISTENING" in text, name
        assert text.index('findstr /i "python"') < text.index("taskkill"), name
    text = BAT.read_text(encoding="ascii")
    assert text.index("call :stop_old") < text.index("uvicorn roundtable.api.app:app")


def test_bat_files_use_crlf():
    for name in ("start.bat", "update_and_start.bat", "stop.bat"):
        data = (BAT.parent / name).read_bytes()
        assert b"\n" not in data.replace(b"\r\n", b""), name


def test_stopped_window_closes_quietly():
    # stop.bat（或新启动替换旧服务）停掉的窗口不该报「启动失败」再等按键
    for name in ("start.bat", "update_and_start.bat", "stop.bat"):
        text = (BAT.parent / name).read_text(encoding="ascii")
        assert text.index('type nul > "%STOP_FLAG%"') < text.index("taskkill"), name
    for name in ("start.bat", "update_and_start.bat"):
        text = (BAT.parent / name).read_text(encoding="ascii")
        run = text.index("uvicorn roundtable.api.app:app")
        quiet = text.index('if exist "%STOP_FLAG%" exit /b 0')
        assert text.index('del "%STOP_FLAG%"') < run < quiet
        assert quiet < text.index("if errorlevel 1 goto :error", run)
