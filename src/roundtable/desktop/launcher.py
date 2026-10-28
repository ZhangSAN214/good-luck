"""桌面版启动器：`python -m roundtable.desktop [--lan]`（快捷方式用 pythonw，没有命令行窗口）。

1. 端口 8000 上已经有圆桌在跑（例如 start.bat 开的）：直接打开窗口，关窗口时不停它。
2. 否则在本进程里起服务（8000 被别的程序占用时顺延到 8001～8010），等 /api/status 就绪后打开窗口；
   关掉窗口即停止服务。
3. 窗口优先用 pywebview（Windows 是 Edge WebView2，macOS 是系统 WebKit），
   浏览器数据存在 data/desktop/，立绘设置、背景音乐等浏览器本地存储会保留；
   没装 pywebview 时用 Edge / Chrome 的应用窗口；
   都没有时用默认浏览器打开（这时关浏览器不会停服务，要用 stop.bat 停）。

没有命令行窗口，所以日志写到 data/logs/desktop.log；启动失败时把原因写成网页打开给用户看。
`--check` 只起服务、确认就绪后退出，不开窗口（CI 在 Windows / macOS 上冒烟测试用）。
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from collections.abc import Callable
from pathlib import Path

TITLE = "圆桌"
PREFERRED_PORT = 8000
PORT_TRIES = 11
READY_TIMEOUT_S = 90.0

log = logging.getLogger("roundtable.desktop")


def project_root() -> Path:
    from roundtable.core.runtime import PROJECT_ROOT

    return PROJECT_ROOT


def desktop_dir() -> Path:
    from roundtable.core.runtime import data_dir

    path = data_dir() / "desktop"
    path.mkdir(parents=True, exist_ok=True)
    return path


def probe(url: str, timeout: float = 1.5) -> bool:
    """url 上是不是一个正在运行的圆桌（/api/status 返回 JSON 对象）。"""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/status", timeout=timeout) as resp:
            return resp.status == 200 and isinstance(json.loads(resp.read()), dict)
    except Exception:
        return False


def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(
    host: str,
    preferred: int = PREFERRED_PORT,
    tries: int = PORT_TRIES,
    *,
    is_free: Callable[[str, int], bool] = port_free,
    is_roundtable: Callable[[str], bool] = probe,
) -> tuple[int, bool]:
    """返回 (端口, 是否已有圆桌在跑)。优先复用 / 使用 preferred，平板记住的地址就不会变。"""
    for port in range(preferred, preferred + tries):
        if is_free(host, port):
            return port, False
        if is_roundtable(f"http://127.0.0.1:{port}"):
            return port, True
    raise RuntimeError(f"端口 {preferred}～{preferred + tries - 1} 都被别的程序占用了")


def wait_ready(url: str, alive: Callable[[], bool], timeout: float = READY_TIMEOUT_S) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if probe(url):
            return True
        if not alive():
            return False
        time.sleep(0.3)
    return False


class Server:
    """在后台线程里跑 uvicorn；stop() 让它正常收尾（成员生活、例会等后台任务随之停止）。"""

    def __init__(self, host: str, port: int) -> None:
        import uvicorn

        config = uvicorn.Config(
            "roundtable.api.app:app", host=host, port=port, log_config=None, access_log=False
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._run, name="roundtable-server", daemon=True)
        self.error: BaseException | None = None

    def _run(self) -> None:
        try:
            self._server.run()
        except BaseException as exc:  # noqa: BLE001 —— 启动失败要显示给用户，不能悄悄退出
            self.error = exc
            log.exception("服务启动失败")

    def start(self) -> None:
        self._thread.start()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def stop(self, timeout: float = 15.0) -> None:
        self._server.should_exit = True
        self._thread.join(timeout)


# ---------- 窗口 ----------


def _browser_candidates() -> list[str]:
    if sys.platform == "win32":
        dirs = [
            os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")
        ]
        tails = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
        return [str(Path(d) / t) for t in tails for d in dirs if d]
    if sys.platform == "darwin":
        return [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    return [p for n in ("chromium", "google-chrome", "microsoft-edge") if (p := shutil.which(n))]


def open_with_webview(url: str, storage: Path) -> bool:
    try:
        import webview
    except ImportError:
        return False
    webview.create_window(TITLE, url, width=1360, height=880, min_size=(900, 600))
    # private_mode=False：保留浏览器本地存储（立绘位置、音乐、时钟等设置只存在本机浏览器里）
    webview.start(private_mode=False, storage_path=str(storage / "webview"))
    return True


def open_with_app_browser(url: str, storage: Path) -> bool:
    exe = next((p for p in _browser_candidates() if Path(p).is_file()), None)
    if exe is None:
        return False
    # 独立的用户数据目录：窗口是单独的浏览器进程，关掉它 wait() 才返回
    args = [
        exe,
        f"--app={url}",
        f"--user-data-dir={storage / 'browser'}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    subprocess.Popen(args).wait()
    return True


def open_window(url: str, storage: Path) -> bool:
    """打开窗口并等它关闭；返回 False 表示只能交给默认浏览器（不会等关闭）。"""
    for opener in (open_with_webview, open_with_app_browser):
        try:
            if opener(url, storage):
                return True
        except Exception:
            log.exception("%s 打不开窗口，换下一种", opener.__name__)
    webbrowser.open(url)
    return False


def show_error(message: str, storage: Path) -> None:
    log_file = storage.parent / "logs" / "desktop.log"
    page = storage / "error.html"
    page.write_text(
        "<!doctype html><meta charset='utf-8'><title>圆桌启动失败</title>"
        "<body style='font-family:sans-serif;max-width:720px;margin:40px auto;line-height:1.6'>"
        f"<h2>圆桌没能启动</h2><pre style='white-space:pre-wrap'>{html.escape(message)}</pre>"
        f"<p>详细日志：{html.escape(str(log_file))}</p>"
        "<p>常见原因：.env 或 config/ 有错、端口被占用、依赖没装好"
        "（重新双击 install_desktop）。</p>",
        encoding="utf-8",
    )
    webbrowser.open(page.as_uri())


# ---------- 入口 ----------


def _setup_logging() -> Path:
    from roundtable.core.runtime import data_dir

    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_file = logs / "desktop.log"
    if log_file.exists() and log_file.stat().st_size > 5_000_000:
        log_file.replace(logs / "desktop.old.log")
    stream = open(log_file, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 —— 进程结束前一直用
    # pythonw 没有控制台：sys.stdout / stderr 为 None，第三方库一打印就会崩
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(stream)],
        force=True,
    )
    return log_file


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="roundtable-desktop", description="圆桌桌面版")
    p.add_argument(
        "--lan", action="store_true", help="同时对家里 Wi-Fi 开放（平板 / 手机，见 LAN.md）"
    )
    p.add_argument(
        "--tailscale-only",
        action="store_true",
        help="只对 Tailscale 里的设备开放（家里 Wi-Fi 的其他设备连不上，见 LAN.md）",
    )
    p.add_argument("--port", type=int, default=PREFERRED_PORT, help="首选端口（默认 8000）")
    p.add_argument("--check", action="store_true", help="只起服务、确认就绪后退出（不开窗口）")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.chdir(project_root())
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # 必须在导入 roundtable.api.app 之前设置：应用在导入时决定是否开放局域网
    if args.tailscale_only:
        os.environ["ROUNDTABLE_LAN"] = "tailscale"
    elif args.lan:
        os.environ["ROUNDTABLE_LAN"] = "1"
    shared = args.lan or args.tailscale_only
    host = "0.0.0.0" if shared else "127.0.0.1"
    log_file = _setup_logging()
    storage = desktop_dir()

    try:
        port, running = choose_port(host, args.port)
    except RuntimeError as exc:
        show_error(str(exc), storage)
        return 1
    url = f"http://127.0.0.1:{port}/"

    server: Server | None = None
    if running:
        log.info("复用已在运行的圆桌：%s", url)
    else:
        log.info("启动服务 %s:%s（局域网 %s）", host, port, os.environ.get("ROUNDTABLE_LAN", "关"))
        server = Server(host, port)
        server.start()
        if not wait_ready(url, server.alive):
            server.stop(timeout=5)
            reason = repr(server.error) if server.error else "服务长时间没有就绪"
            show_error(f"{reason}\n\n日志：{log_file}", storage)
            return 1

    try:
        if args.check:
            print(f"ok {url}")
            return 0
        if not open_window(url, storage) and server is not None:
            # 只能交给默认浏览器：服务一直跑，直到 stop.bat 或注销
            log.info("已用默认浏览器打开；停止服务用 stop.bat")
            while server.alive():
                time.sleep(1)
        return 0
    finally:
        if server is not None:
            log.info("退出，停止服务")
            server.stop()
