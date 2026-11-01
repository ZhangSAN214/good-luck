"""Windows 一键启动脚本：进入项目目录、激活 .venv、更新、安装、启动服务并打开浏览器。"""

from __future__ import annotations

from pathlib import Path

BAT = Path(__file__).resolve().parents[2] / "start.bat"


def test_start_bat_steps_in_order():
    text = BAT.read_text(encoding="ascii")  # 纯 ASCII：不受控制台代码页影响
    steps = [
        'cd /d "%~dp0"',
        r".venv\Scripts\activate.bat",
        r"python scripts\safe_update.py update --extras dev,windows --ff-only",
        r"python scripts\install_check.py --source start --extras dev,windows",
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
    assert text.index("GIT_TERMINAL_PROMPT=0") < text.index("safe_update.py update")
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
    for name in ("start.bat", "update_and_start.bat", "stop.bat", "undo_update.bat"):
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


def test_updates_go_through_safe_update():
    """更新前存档点、白名单、冒烟测试、失败退回都在 safe_update.py 里（docs/SAFE_UPDATE.md）；
    批处理里不再直接 git pull / pip install，退回旧版本时照常启动。"""
    for name, args in (
        ("start.bat", "--extras dev,windows --ff-only"),
        ("update_and_start.bat", "--extras dev,media --strict"),
    ):
        text = (BAT.parent / name).read_text(encoding="ascii")
        code = [
            ln for ln in text.splitlines() if not ln.lstrip().lower().startswith(("rem", "echo"))
        ]
        body = "\n".join(code)
        assert f"safe_update.py update {args}" in body, name
        assert "git pull" not in body and "pip install" not in body, name
        assert '"%UPD_RC%"=="1" goto :error' in body, name
        assert '"%UPD_RC%"=="2" echo [warn]' in body, name


def test_install_check_runs_after_old_server_stops():
    for name, source, stop in (
        ("start.bat", "start", "call :stop_old"),
        ("update_and_start.bat", "update", "taskkill"),
    ):
        text = (BAT.parent / name).read_text(encoding="ascii")
        check = text.index(f"install_check.py --source {source}")
        assert text.index(stop) < check < text.index("uvicorn roundtable.api.app:app"), name


def test_undo_update_bat_stops_server_first():
    text = (BAT.parent / "undo_update.bat").read_text(encoding="ascii")
    assert text.index('findstr /i "python"') < text.index("taskkill")
    assert text.index("taskkill") < text.index("safe_update.py undo")
    assert "pause" in text and "git reset" not in text
