import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from roundtable.desktop import launcher


def test_choose_port_prefers_free_8000():
    port, running = launcher.choose_port(
        "127.0.0.1", 8000, is_free=lambda h, p: True, is_roundtable=lambda u: False
    )
    assert (port, running) == (8000, False)


def test_choose_port_reuses_running_roundtable():
    port, running = launcher.choose_port(
        "127.0.0.1", 8000, is_free=lambda h, p: False, is_roundtable=lambda u: u.endswith(":8000")
    )
    assert (port, running) == (8000, True)


def test_choose_port_skips_ports_used_by_other_programs():
    port, running = launcher.choose_port(
        "127.0.0.1", 8000, is_free=lambda h, p: p == 8003, is_roundtable=lambda u: False
    )
    assert (port, running) == (8003, False)


def test_choose_port_gives_up_with_readable_error():
    with pytest.raises(RuntimeError, match="8000～8002"):
        launcher.choose_port(
            "127.0.0.1", 8000, 3, is_free=lambda h, p: False, is_roundtable=lambda u: False
        )


def test_port_free_detects_listener():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        assert not launcher.port_free("127.0.0.1", s.getsockname()[1])


def _serve(body: bytes):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.mark.parametrize(("body", "expected"), [(b'{"ok": true}', True), (b"<html>", False)])
def test_probe_only_accepts_roundtable_status(body, expected):
    httpd = _serve(body)
    try:
        assert launcher.probe(f"http://127.0.0.1:{httpd.server_port}") is expected
    finally:
        httpd.shutdown()


def test_probe_nothing_listening():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert launcher.probe(f"http://127.0.0.1:{port}", timeout=0.5) is False


def test_wait_ready_stops_when_server_dies():
    assert launcher.wait_ready("http://127.0.0.1:9", alive=lambda: False, timeout=5) is False


def test_open_window_falls_back_to_default_browser(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(launcher, "open_with_webview", lambda u, s: False)
    monkeypatch.setattr(launcher, "open_with_app_browser", lambda u, s: 1 / 0)
    monkeypatch.setattr(launcher.webbrowser, "open", opened.append)
    assert launcher.open_window("http://127.0.0.1:8000/", tmp_path) is False
    assert opened == ["http://127.0.0.1:8000/"]


def test_app_browser_uses_own_profile(monkeypatch, tmp_path):
    exe = tmp_path / "msedge.exe"
    exe.write_text("")
    calls = []

    class Proc:
        def __init__(self, args):
            calls.append(args)

        def wait(self):
            return 0

    monkeypatch.setattr(launcher, "_browser_candidates", lambda: [str(exe)])
    monkeypatch.setattr(launcher.subprocess, "Popen", Proc)
    assert launcher.open_with_app_browser("http://127.0.0.1:8000/", tmp_path)
    args = calls[0]
    assert "--app=http://127.0.0.1:8000/" in args
    assert f"--user-data-dir={tmp_path / 'browser'}" in args


def test_lan_flag_is_opt_in():
    assert launcher.parse_args([]).lan is False
    assert launcher.parse_args(["--lan"]).lan is True
    assert launcher.parse_args(["--tailscale-only"]).tailscale_only is True
