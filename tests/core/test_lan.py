"""局域网访问：口令哈希、登录 cookie 过期、换口令失效、限速、一次性配对码、地址筛选。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.lan import (
    FREE_TRIES,
    LOCK_MAX_S,
    PAIR_SECONDS,
    SESSION_DAYS,
    LanAccess,
    firewall_command,
    is_local,
    is_remote,
    is_tailnet,
    lan_addresses,
    lan_enabled,
    lan_mode,
    qr_svg,
    tailscale_addresses,
    tailscale_firewall_command,
)

PW = "home-" + "pass"


class Clock:
    def __init__(self) -> None:
        self.t = 1_800_000_000.0

    def __call__(self) -> float:
        return self.t


def access(tmp_path, **kw) -> tuple[LanAccess, Clock]:
    clock = Clock()
    return LanAccess(tmp_path / "lan_access.json", clock=clock, **kw), clock


def test_password_is_salted_hash_only(tmp_path):
    lan, _ = access(tmp_path)
    assert not lan.has_password
    lan.set_password(PW)
    text = (tmp_path / "lan_access.json").read_text(encoding="utf-8")
    assert PW not in text
    data = json.loads(text)
    assert set(data) >= {"salt", "hash", "secret"}
    # 同样的口令再设一次，盐不同、哈希也不同
    lan.set_password(PW)
    assert json.loads((tmp_path / "lan_access.json").read_text())["hash"] != data["hash"]
    # 重新读文件后口令照样能用
    again = LanAccess(tmp_path / "lan_access.json")
    assert again.login(PW, "10.0.0.2").ok


@pytest.mark.parametrize("bad", ["", "12345", "x" * 129])
def test_password_length(tmp_path, bad):
    lan, _ = access(tmp_path)
    with pytest.raises(ValueError):
        lan.set_password(bad)


def test_login_cookie_expires_and_password_change_signs_everyone_out(tmp_path):
    lan, clock = access(tmp_path)
    assert lan.login(PW, "c").reason == "no_password"
    lan.set_password(PW)
    result = lan.login(PW, "c")
    assert result.ok and lan.verify(result.token)
    assert not lan.verify(None) and not lan.verify("garbage") and not lan.verify("1.2")
    exp, sig = result.token.split(".")
    assert not lan.verify(f"{int(exp) + 1}.{sig}")  # 改过期时间，签名对不上
    clock.t += SESSION_DAYS * 86400 + 1
    assert not lan.verify(result.token)

    token = lan.login(PW, "c").token
    lan.sign_out_all()
    assert not lan.verify(token) and lan.login(PW, "c").ok  # 口令不变
    token = lan.login(PW, "c").token
    lan.set_password("another-" + "pass")
    assert not lan.verify(token) and not lan.login(PW, "c").ok


def test_wrong_password_rate_limited_per_device(tmp_path):
    lan, clock = access(tmp_path)
    lan.set_password(PW)
    for _ in range(FREE_TRIES - 1):
        assert lan.login("wrong-pass", "10.0.0.9").reason == "wrong"
    locked = lan.login("wrong-pass", "10.0.0.9")
    assert locked.reason == "locked" and locked.retry_after == 60
    # 锁定期间连正确口令也不行；别的设备不受影响
    assert lan.login(PW, "10.0.0.9").reason == "locked"
    assert lan.login(PW, "10.0.0.8").ok
    clock.t += 61
    assert lan.login("wrong-pass", "10.0.0.9").retry_after == 120  # 再错一次翻倍
    for _ in range(10):
        clock.t += LOCK_MAX_S + 1
        last = lan.login("wrong-pass", "10.0.0.9")
    assert last.retry_after == LOCK_MAX_S
    clock.t += LOCK_MAX_S + 1
    assert lan.login(PW, "10.0.0.9").ok
    assert lan.login("wrong-pass", "10.0.0.9").reason == "wrong"  # 登录成功后计数清零


def test_pair_code_single_use_and_expires(tmp_path):
    lan, clock = access(tmp_path)
    with pytest.raises(ValueError):
        lan.new_pair_code()
    lan.set_password(PW)
    code = lan.new_pair_code()
    token = lan.redeem_pair_code(code)
    assert token and lan.verify(token)
    assert lan.redeem_pair_code(code) is None
    assert lan.redeem_pair_code(None) is None and lan.redeem_pair_code("nope") is None
    late = lan.new_pair_code()
    clock.t += PAIR_SECONDS + 1
    assert lan.redeem_pair_code(late) is None


def test_corrupt_file_means_no_password(tmp_path):
    (tmp_path / "lan_access.json").write_text("{not json", encoding="utf-8")
    assert not LanAccess(tmp_path / "lan_access.json").has_password
    (tmp_path / "lan_access.json").write_text('{"hash": "00"}', encoding="utf-8")
    assert not LanAccess(tmp_path / "lan_access.json").has_password


def test_addresses_and_locality():
    probe = lambda: ["127.0.0.1", "8.8.8.8", "10.1.2.3", "192.168.1.5", "169.254.1.1", "::1", "x"]  # noqa: E731
    assert lan_addresses(probe) == ["192.168.1.5", "10.1.2.3"]
    assert is_local("127.0.0.1") and is_local("::1") and is_local("::ffff:127.0.0.1")
    assert not is_local("192.168.1.5") and not is_local(None) and not is_local("testclient")
    assert is_remote("192.168.1.5") and not is_remote("127.0.0.1") and not is_remote("testclient")


def test_enabled_flag_and_status(tmp_path):
    assert not lan_enabled({}) and not lan_enabled({"ROUNDTABLE_LAN": "0"})
    assert lan_enabled({"ROUNDTABLE_LAN": "1"}) and lan_enabled({"ROUNDTABLE_LAN": " On "})
    lan = LanAccess.from_env(tmp_path, {"ROUNDTABLE_LAN": "1"})
    st = lan.status(8000, ["192.168.1.5"], [])
    assert st["enabled"] and not st["has_password"] and not st["tailscale_only"]
    assert st["urls"] == ["http://192.168.1.5:8000"] and st["tailscale_urls"] == []
    assert "-LocalPort 8000" in st["firewall"] and "-Profile Private" in st["firewall"]
    assert firewall_command(8799).count("8799") == 2


def test_qr_svg_scales():
    svg = qr_svg("http://192.168.1.5:8000/lan/pair?c=abc")
    assert svg.startswith("<svg") and "viewBox" in svg and "width=" not in svg.split(">", 1)[0]


def test_tailscale_addresses_and_mode(tmp_path):
    probe = lambda: ["192.168.1.5", "100.101.102.103", "100.128.0.1", "100.63.255.255"]  # noqa: E731
    assert tailscale_addresses(probe) == ["100.101.102.103"]
    # 100.64.0.0/10 不是「私有」网段，家里局域网的地址里不列它
    assert lan_addresses(probe) == ["192.168.1.5"]
    assert (
        is_tailnet("100.64.0.1") and is_tailnet("fd7a:115c:a1e0::5") and not is_tailnet("10.0.0.1")
    )
    assert is_tailnet("::ffff:100.100.1.1") and not is_tailnet(None) and not is_tailnet("x")
    assert lan_mode({}) == "off" and lan_mode({"ROUNDTABLE_LAN": "1"}) == "lan"
    assert lan_mode({"ROUNDTABLE_LAN": " Tailscale "}) == "tailscale"
    assert lan_enabled({"ROUNDTABLE_LAN": "tailscale"})

    both = LanAccess.from_env(tmp_path, {"ROUNDTABLE_LAN": "1"})
    st = both.status(8000, ["192.168.1.5"], ["100.101.102.103"])
    assert st["urls"] == ["http://192.168.1.5:8000", "http://100.101.102.103:8000"]
    assert st["tailscale_urls"] == ["http://100.101.102.103:8000"]
    assert both.allows("192.168.1.20") and both.allows("100.101.1.1")

    only = LanAccess.from_env(tmp_path, {"ROUNDTABLE_LAN": "tailscale"})
    st = only.status(8000, ["192.168.1.5"], ["100.101.102.103"])
    assert st["tailscale_only"] and st["urls"] == ["http://100.101.102.103:8000"]
    assert only.allows("100.101.1.1") and not only.allows("192.168.1.20")


def test_tailscale_firewall_only_opens_tailnet_range():
    cmd = tailscale_firewall_command(8000)
    assert "-RemoteAddress 100.64.0.0/10" in cmd and "-LocalPort 8000" in cmd
    assert "-Profile" not in cmd
