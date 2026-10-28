"""局域网访问的守门：默认不对别的设备开放；开放后别的设备要登录，设置只能在电脑本机上做。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from roundtable.api.app import create_app
from roundtable.core.service import LAN_COOKIE, LanAccess, RoundtableService

from ..core.orchestrator.conftest import Env

PW = "home-" + "pass"
PC = ("127.0.0.1", 50000)
PAD = ("192.168.1.20", 50001)
BASE = "http://192.168.1.5:8000"


def app_with(tmp_path, enabled: bool, tailscale_only: bool = False):
    lan = LanAccess(tmp_path / "lan_access.json", enabled=enabled, tailscale_only=tailscale_only)
    lan.status = _fixed_status(lan.status)  # 不读真实网卡
    return create_app(RoundtableService(Env().rt), lan=lan), lan


def _fixed_status(original):
    return lambda port, addresses=None, tailnet=None: original(
        port, ["192.168.1.5"], ["100.101.102.103"]
    )


def clients(app):
    pc = TestClient(app, client=PC, base_url="http://127.0.0.1:8000", follow_redirects=False)
    pad = TestClient(app, client=PAD, base_url=BASE, follow_redirects=False)
    return pc, pad


def test_default_refuses_other_devices(tmp_path):
    app, _ = app_with(tmp_path, enabled=False)
    pc, pad = clients(app)
    with pc, pad:
        assert pc.get("/api/status").status_code == 200
        assert pad.get("/api/status").status_code == 403
        assert pad.get("/").status_code == 403
        assert pad.post("/api/lan/login", json={"password": PW}).status_code == 403
        assert pc.get("/api/lan").json()["enabled"] is False


def test_lan_requires_login_and_settings_stay_on_pc(tmp_path):
    app, lan = app_with(tmp_path, enabled=True)
    pc, pad = clients(app)
    with pc, pad:
        # 本机照常；平板没登录：页面跳登录页、接口 401；登录页与图标可以打开
        assert pc.get("/api/status").status_code == 200
        r = pad.get("/")
        assert r.status_code == 303 and r.headers["location"] == "/login.html"
        assert pad.get("/api/status").status_code == 401
        assert pad.get("/login.html").status_code == 200
        assert pad.get("/manifest.webmanifest").status_code == 200
        assert pad.get("/img/app/icon-192.png").status_code == 200
        # 还没设口令
        assert pad.post("/api/lan/login", json={"password": PW}).status_code == 403
        # 设置只能在电脑上
        assert pad.post("/api/lan/password", json={"password": PW}).status_code == 401
        assert pc.post("/api/lan/password", json={"password": "123"}).status_code == 400
        assert pc.post("/api/lan/password", json={"password": PW}).status_code == 200
        st = pc.get("/api/lan").json()
        assert st["enabled"] and st["has_password"] and st["urls"][0] == BASE

        assert pad.post("/api/lan/login", json={"password": "nope-nope"}).status_code == 401
        ok = pad.post("/api/lan/login", json={"password": PW})
        assert ok.status_code == 200 and LAN_COOKIE in ok.cookies
        cookie = ok.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert pad.get("/api/status").status_code == 200
        assert pad.get("/").status_code == 200
        # 登录了也不能改设置
        assert pad.get("/api/lan").status_code == 403
        assert pad.post("/api/lan/signout").status_code == 403

        # 电脑上「让所有平板退出」
        assert pc.post("/api/lan/signout").status_code == 200
        assert pad.get("/api/status").status_code == 401
        assert lan.has_password


def test_rate_limit_answers_429(tmp_path):
    app, lan = app_with(tmp_path, enabled=True)
    lan.set_password(PW)
    _, pad = clients(app)
    with pad:
        codes = [
            pad.post("/api/lan/login", json={"password": "wrong-pass"}).status_code
            for _ in range(5)
        ]
        assert codes == [401, 401, 401, 401, 429]
        locked = pad.post("/api/lan/login", json={"password": PW})
        assert locked.status_code == 429 and int(locked.headers["retry-after"]) > 0


def test_pair_link_logs_in_once(tmp_path):
    app, _ = app_with(tmp_path, enabled=True)
    pc, pad = clients(app)
    with pc, pad:
        assert pc.post("/api/lan/pair", json={"base": BASE}).status_code == 400  # 先设口令
        pc.post("/api/lan/password", json={"password": PW})
        assert pc.post("/api/lan/pair", json={"base": "http://evil.example"}).status_code == 400
        pair = pc.post("/api/lan/pair", json={"base": BASE}).json()
        assert pair["url"].startswith(BASE + "/lan/pair?c=") and pair["qr"].startswith("<svg")
        path = pair["url"][len(BASE) :]
        r = pad.get(path)
        assert r.status_code == 303 and r.headers["location"] == "/"
        assert pad.get("/api/status").status_code == 200
        pad.cookies.clear()
        again = pad.get(path)
        assert again.headers["location"].startswith("/login.html")
        assert pad.get("/api/status").status_code == 401


def test_dns_rebinding_host_refused_when_open(tmp_path):
    app, _ = app_with(tmp_path, enabled=True)
    pc = TestClient(app, client=PC, base_url="http://evil.example:8000")
    with pc:
        assert pc.get("/api/status").status_code == 403
    ok = TestClient(app, client=PC, base_url="http://localhost:8000")
    with ok:
        assert ok.get("/api/status").status_code == 200


def test_tailscale_phone_logs_in_and_pairs(tmp_path):
    app, _ = app_with(tmp_path, enabled=True)
    pc = TestClient(app, client=PC, base_url="http://127.0.0.1:8000")
    phone = TestClient(app, client=("100.90.1.2", 50002), base_url="http://100.101.102.103:8000")
    magic = TestClient(app, client=("100.90.1.2", 50003), base_url="http://pc.tail1234.ts.net:8000")
    with pc, phone, magic:
        assert pc.post("/api/lan/password", json={"password": PW}).status_code == 200
        st = pc.get("/api/lan").json()
        assert st["tailscale_urls"] == ["http://100.101.102.103:8000"]
        r = pc.post("/api/lan/pair", json={"base": "http://100.101.102.103:8000"})
        assert r.status_code == 200 and r.json()["url"].startswith("http://100.101.102.103:8000/")
        assert phone.post("/api/lan/login", json={"password": PW}).status_code == 200
        assert phone.get("/api/status").status_code == 200
        # MagicDNS 的名字也能打开（还是要登录）
        assert magic.get("/api/status").status_code == 401


def test_tailscale_only_refuses_home_wifi(tmp_path):
    app, _ = app_with(tmp_path, enabled=True, tailscale_only=True)
    pc, pad = clients(app)
    phone = TestClient(app, client=("100.90.1.2", 50002), base_url="http://100.101.102.103:8000")
    with pc, pad, phone:
        assert pc.post("/api/lan/password", json={"password": PW}).status_code == 200
        assert pad.post("/api/lan/login", json={"password": PW}).status_code == 403
        assert pad.get("/login.html").status_code == 403
        assert phone.post("/api/lan/login", json={"password": PW}).status_code == 200
        assert pc.get("/api/lan").json()["urls"] == ["http://100.101.102.103:8000"]
        assert pc.get("/api/status").status_code == 200
