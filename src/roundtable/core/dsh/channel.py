"""dsh 调模型用的 key 和余额查询。

key 只放在项目根目录 ``.env``（或环境变量），读进来包成 ``Secret``；只有这个文件调用 ``reveal()``：
一处是启动 dsh 时放进子进程的环境变量（dsh 自己的模型适配器组装请求头），另一处是查余额时的请求头。
key 不写进任何文件，也不出现在日志、状态和 API 响应里。

子进程的环境变量从空白开始，只带系统必需的几个和 dsh 自己的变量：圆桌的其他 key 一个都不给。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from roundtable.core.providers.secrets import KeyRing, Secret, redact

# 子进程只继承这些系统变量（大小写不敏感；Windows 上 Node 和 PowerShell 需要它们）
BASE_ENV = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE",
    "OS",
    "LANG",
    "LC_ALL",
    "TZ",
)


class BalanceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Balance:
    amount: float  # 换算成美元后的总余额
    currency: str  # 原始币种
    raw: float  # 原始币种的金额


class ModelKey:
    """dsh 用的模型 key：没配就是 None（工作台照样能开，只是不能调模型）。"""

    def __init__(self, key_env: str | None, secret: Secret | None) -> None:
        self.key_env = key_env
        self._secret = secret

    @classmethod
    def load(
        cls,
        key_env: str | None,
        *,
        environ: Mapping[str, str] | None = None,
        dotenv_path: Path | str | None = None,
    ) -> ModelKey:
        if not key_env:
            return cls(None, None)
        ring = KeyRing.from_env([key_env], environ=environ, dotenv_path=dotenv_path)
        return cls(key_env, ring.get(key_env))

    @property
    def present(self) -> bool:
        return self._secret is not None

    def child_env(
        self, extra: Mapping[str, str], environ: Mapping[str, str] | None = None
    ) -> dict[str, str]:
        """dsh 子进程的完整环境变量：系统必需的几个 + dsh 自己的 + 模型 key。"""
        source = os.environ if environ is None else environ
        wanted = {name.upper() for name in BASE_ENV}
        env = {k: v for k, v in source.items() if k.upper() in wanted}
        env.update(extra)
        if self.key_env and self._secret is not None:
            env[self.key_env] = self._secret.reveal()
        return env

    async def balance(
        self,
        url: str,
        usd_rates: Mapping[str, float],
        *,
        timeout_s: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> Balance:
        """查余额（返回 ``balance_infos`` 列表的接口），换算成美元。"""
        if self._secret is None:
            raise BalanceError(f"没有配置 {self.key_env}，查不了余额")
        headers = {"Authorization": f"Bearer {self._secret.reveal()}", "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=timeout_s, transport=transport) as client:
                resp = await client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise BalanceError(f"查余额失败：{type(exc).__name__}") from None
        if resp.status_code != 200:
            detail = redact(resp.text[:200], [self._secret.reveal()])
            raise BalanceError(f"查余额失败：HTTP {resp.status_code} {detail}") from None
        try:
            return parse_balance(resp.json(), usd_rates)
        except (ValueError, TypeError, KeyError) as exc:
            raise BalanceError(f"余额接口返回的格式看不懂：{exc}") from None


def parse_balance(data: Any, usd_rates: Mapping[str, float]) -> Balance:
    infos = data.get("balance_infos") if isinstance(data, dict) else None
    if not infos:
        raise ValueError("没有 balance_infos")
    # 有美元余额时优先用美元；否则用第一个能换算的币种
    ordered = sorted(infos, key=lambda i: str(i.get("currency", "")).upper() != "USD")
    for info in ordered:
        currency = str(info.get("currency", "")).upper()
        raw = float(info.get("total_balance"))
        if currency == "USD":
            return Balance(raw, currency, raw)
        rate = usd_rates.get(currency)
        if rate:
            return Balance(raw / rate, currency, raw)
    raise ValueError(
        f"不认识的币种 {[i.get('currency') for i in infos]}（在 dsh.usd_rates 里加汇率）"
    )
