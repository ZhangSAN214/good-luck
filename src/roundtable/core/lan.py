"""局域网访问（阶段 35 的一部分）：让同一 Wi-Fi 下的平板 / 手机打开电脑上的圆桌。

- 默认关闭：服务只监听本机；`start_lan.bat`
  （或环境变量 ROUNDTABLE_LAN=1 + `--host 0.0.0.0`）才打开。
- 打开后，本机（127.0.0.1 / ::1）照常使用；其他设备必须先登录：
  访问口令只存加盐哈希（scrypt），登录后发带过期时间的签名 cookie；连续输错按设备限速。
- 也可以在电脑上生成一次性配对链接（二维码），平板扫码即登录，10 分钟内有效、用一次作废。
- 口令文件在数据库旁边（data/lan_access.json，不进 git）；改口令会让所有已登录的设备退出。
- 离家用：Tailscale 私有组网（WireGuard）。电脑的 100.x 地址也列给平板；Tailscale 不是公网，
  只有用户账号里的设备能连。ROUNDTABLE_LAN=tailscale 时只接受 Tailscale 里的设备
  （家里 Wi-Fi 也不行）。
  不用 Tailscale 的 Funnel（公网暴露）和 Serve（经本机反代，请求都像来自本机，会绕过登录）。

纯逻辑，不依赖 Web 框架；时钟可注入，便于测试过期与限速。
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LAN_ENV = "ROUNDTABLE_LAN"
LAN_FILE = "lan_access.json"
COOKIE = "rt_lan"
MIN_PASSWORD = 6
MAX_PASSWORD = 128
SESSION_DAYS = 30
PAIR_SECONDS = 600
# 连续输错 FREE_TRIES 次后开始锁定：60 秒起每次翻倍，最多 15 分钟
FREE_TRIES = 5
LOCK_BASE_S = 60
LOCK_MAX_S = 900
# Tailscale 给设备分的地址段（CGNAT 段与它固定的 IPv6 前缀）
TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
TAILNET_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
# scrypt 参数（约 16 MB 内存）：登录本来就限速，这里只防口令文件被拿走后被快速爆破
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def lan_mode(env: dict[str, str] | None = None) -> str:
    """返回 off（默认，只有本机）、lan（家里局域网 + Tailscale）或 tailscale（只许 Tailscale）。"""
    value = (env if env is not None else os.environ).get(LAN_ENV, "").strip().lower()
    if value == "tailscale":
        return "tailscale"
    return "lan" if value in {"1", "true", "yes", "on"} else "off"


def lan_enabled(env: dict[str, str] | None = None) -> bool:
    return lan_mode(env) != "off"


def is_tailnet(host: str | None) -> bool:
    """Tailscale 里的设备地址（100.64.0.0/10 或 fd7a:115c:a1e0::/48）。"""
    ip = _ip(host)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip is not None and (ip in TAILNET_V4 or ip in TAILNET_V6)


def is_local(host: str | None) -> bool:
    """本机地址（127.0.0.0/8、::1、IPv4 映射的回环）。"""
    ip = _ip(host)
    if ip is None:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def is_remote(host: str | None) -> bool:
    """确定是别的设备（能解析成 IP 且不是本机）。没有 IP 的连接（测试客户端、unix 套接字）不算。"""
    return _ip(host) is not None and not is_local(host)


def _ip(host: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address((host or "").split("%", 1)[0])
    except ValueError:
        return None


def lan_addresses(probe: Callable[[], list[str]] | None = None) -> list[str]:
    """这台电脑在局域网里的 IPv4 地址（家庭网络常见的私有网段在前）。只查本机网卡，不联网。"""
    found: list[str] = []
    for raw in (probe or _probe_addresses)():
        ip = _ip(raw)
        if not isinstance(ip, ipaddress.IPv4Address) or ip.is_loopback or ip.is_link_local:
            continue
        if ip.is_private and raw not in found:
            found.append(raw)
    # 192.168.x.x 最常见于家用路由器，排在最前；其次 10.x、172.16-31.x
    return sorted(found, key=lambda a: (not a.startswith("192.168."), not a.startswith("10."), a))


def tailscale_addresses(probe: Callable[[], list[str]] | None = None) -> list[str]:
    """这台电脑在 Tailscale 里的 IPv4 地址（装了 Tailscale 并登录后才有）。只查本机网卡。"""
    found: list[str] = []
    for raw in (probe or _probe_addresses)():
        ip = _ip(raw)
        if isinstance(ip, ipaddress.IPv4Address) and is_tailnet(raw) and raw not in found:
            found.append(raw)
    return found


def _probe_addresses() -> list[str]:
    out: list[str] = []
    try:
        # UDP 的 connect 不发任何数据包，只让系统选出默认出口网卡的地址
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # TEST-NET-1，保留地址
            out.append(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.append(str(info[4][0]))
    except OSError:
        pass
    try:
        import psutil  # 各网卡的地址（Tailscale 的虚拟网卡不一定出现在上面两种查法里）

        for addrs in psutil.net_if_addrs().values():
            out.extend(a.address for a in addrs if a.family == socket.AF_INET)
    except Exception:  # noqa: BLE001 —— 查不到就少列一个地址，不影响使用
        pass
    return out


@dataclass
class LoginResult:
    ok: bool
    token: str | None = None
    retry_after: int = 0  # 被锁定时还要等几秒
    reason: str = ""


@dataclass
class _Tries:
    failures: int = 0
    locked_until: float = 0.0


@dataclass
class LanAccess:
    """口令、登录 cookie、配对链接与限速。enabled = 这次启动是否对局域网开放。"""

    path: Path | None
    enabled: bool = False
    tailscale_only: bool = False  # 只接受 Tailscale 里的设备（ROUNDTABLE_LAN=tailscale）
    clock: Callable[[], float] = time.time
    _state: dict[str, Any] = field(default_factory=dict)
    _tries: dict[str, _Tries] = field(default_factory=dict)
    _pairs: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._state = self._load()

    @classmethod
    def from_env(cls, data_dir: Path, env: dict[str, str] | None = None) -> LanAccess:
        mode = lan_mode(env)
        return cls(
            path=data_dir / LAN_FILE, enabled=mode != "off", tailscale_only=mode == "tailscale"
        )

    def allows(self, client: str | None) -> bool:
        """这台设备能不能连进来（还要登录）：只许 Tailscale 时，家里局域网的设备也不行。"""
        return not self.tailscale_only or is_tailnet(client)

    # ---- 口令 ----

    @property
    def has_password(self) -> bool:
        return bool(self._state.get("hash"))

    def set_password(self, password: str) -> None:
        """设置 / 修改口令；同时换签名密钥，所有已登录的设备都要重新登录。"""
        if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
            raise ValueError(f"口令长度要在 {MIN_PASSWORD}～{MAX_PASSWORD} 个字符之间")
        salt = secrets.token_bytes(16)
        self._state = {
            "version": 1,
            "salt": salt.hex(),
            "hash": _hash(password, salt).hex(),
            "secret": secrets.token_hex(32),
            "updated_at": int(self.clock()),
        }
        self._pairs.clear()
        self._save()

    def sign_out_all(self) -> None:
        """让所有已登录的设备退出（口令不变）。"""
        if self.has_password:
            self._state["secret"] = secrets.token_hex(32)
            self._pairs.clear()
            self._save()

    def login(self, password: str, client: str) -> LoginResult:
        now = self.clock()
        tries = self._tries.setdefault(client, _Tries())
        if tries.locked_until > now:
            wait = int(tries.locked_until - now) + 1
            return LoginResult(False, retry_after=wait, reason="locked")
        if not self.has_password:
            return LoginResult(False, reason="no_password")
        expected = bytes.fromhex(self._state["hash"])
        given = _hash(password[:MAX_PASSWORD], bytes.fromhex(self._state["salt"]))
        if hmac.compare_digest(expected, given):
            self._tries.pop(client, None)
            return LoginResult(True, token=self.issue())
        tries.failures += 1
        over = tries.failures - FREE_TRIES
        if over >= 0:
            tries.locked_until = now + min(LOCK_BASE_S * 2**over, LOCK_MAX_S)
            return LoginResult(False, retry_after=int(tries.locked_until - now), reason="locked")
        return LoginResult(False, reason="wrong")

    # ---- 登录 cookie：「过期时间.签名」，换密钥即全部失效 ----

    def issue(self) -> str:
        exp = int(self.clock()) + SESSION_DAYS * 86400
        return f"{exp}.{self._sign(str(exp))}"

    def verify(self, token: str | None) -> bool:
        if not token or not self.has_password or "." not in token:
            return False
        exp, sig = token.split(".", 1)
        if not exp.isdigit() or int(exp) < self.clock():
            return False
        return hmac.compare_digest(sig, self._sign(exp))

    def _sign(self, payload: str) -> str:
        key = bytes.fromhex(self._state["secret"])
        return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()

    def status(
        self, port: int, addresses: list[str] | None = None, tailnet: list[str] | None = None
    ) -> dict[str, Any]:
        """给电脑上的「平板」面板看：是否开放、有没有口令、平板该打开的地址、防火墙命令。

        urls 是能生成二维码的全部地址（家里局域网在前，Tailscale 在后）；
        tailscale_urls 是其中的 Tailscale 部分。
        """
        if addresses is None or tailnet is None:
            probed = _probe_addresses()
            addresses = lan_addresses(lambda: probed) if addresses is None else addresses
            tailnet = tailscale_addresses(lambda: probed) if tailnet is None else tailnet
        home = [] if self.tailscale_only else [f"http://{ip}:{port}" for ip in addresses]
        ts = [f"http://{ip}:{port}" for ip in tailnet]
        return {
            "enabled": self.enabled,
            "tailscale_only": self.tailscale_only,
            "has_password": self.has_password,
            "port": port,
            "urls": home + ts,
            "tailscale_urls": ts,
            "firewall": firewall_command(port),
            "tailscale_firewall": tailscale_firewall_command(port),
            "session_days": SESSION_DAYS,
            "pair_minutes": PAIR_SECONDS // 60,
        }

    # ---- 一次性配对链接 ----

    def new_pair_code(self) -> str:
        if not self.has_password:
            raise ValueError("先设访问口令")
        now = self.clock()
        self._pairs = {c: t for c, t in self._pairs.items() if t > now}
        code = secrets.token_urlsafe(18)
        self._pairs[code] = now + PAIR_SECONDS
        return code

    def redeem_pair_code(self, code: str | None) -> str | None:
        """配对码有效时返回登录 cookie（配对码随即作废）；无效返回 None。"""
        until = self._pairs.pop(code or "", None)
        if until is None or until < self.clock():
            return None
        return self.issue()

    # ---- 文件 ----

    def _load(self) -> dict[str, Any]:
        if self.path is None or not self.path.is_file():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        keys = {"salt", "hash", "secret"}
        return data if isinstance(data, dict) and keys <= data.keys() else {}

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def firewall_command(port: int) -> str:
    """Windows 防火墙放行规则（只放行「专用」网络、只这个端口）。

    由用户自己在管理员 PowerShell 里运行；程序从不改系统设置。"""
    return (
        f'New-NetFirewallRule -DisplayName "Roundtable LAN {port}" -Direction Inbound '
        f"-Protocol TCP -LocalPort {port} -Action Allow -Profile Private"
    )


def tailscale_firewall_command(port: int) -> str:
    """只放行 Tailscale 地址段来的连接（Tailscale 的虚拟网卡在 Windows 里不一定是「专用网络」）。"""
    return (
        f'New-NetFirewallRule -DisplayName "Roundtable Tailscale {port}" -Direction Inbound '
        f"-Protocol TCP -LocalPort {port} -RemoteAddress {TAILNET_V4} -Action Allow"
    )


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)


def qr_svg(text: str) -> str:
    """把链接画成二维码（SVG 文本，本机生成，不联网）。"""
    import io

    import segno

    buf = io.BytesIO()
    segno.make(text, error="m").save(
        buf, kind="svg", border=2, xmldecl=False, omitsize=True, dark="#0f1724", light="#ffffff"
    )
    return buf.getvalue().decode("utf-8")
