"""渠道优先顺序与每月额度：读配置 + 界面里的修改（app_settings），本月各渠道已用按本机账本算。"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from roundtable.core.config import AppConfig
from roundtable.core.providers.priority import STRATEGIES, ChannelPriority
from roundtable.core.storage import Repository

from .guard import month_window, week_window

SETTING_KEY = "channels.priority"


def _saved(repo: Repository) -> dict[str, Any]:
    raw = repo.setting(SETTING_KEY)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def load_priority(config: AppConfig, repo: Repository) -> ChannelPriority:
    """配置给默认值，界面里保存过的覆盖它；已不存在的渠道忽略。"""
    channels = config.models.channels
    base = config.roundtable.channel_priority
    saved = _saved(repo)
    strategy = saved.get("strategy", base.strategy)
    if strategy not in STRATEGIES:
        strategy = base.strategy
    order = saved.get("order", base.order)
    if not isinstance(order, list):
        order = base.order
    credits: dict[str, float | None] = {
        name: spec.monthly_credit_usd for name, spec in channels.items()
    }
    overrides = saved.get("credits", {})
    if isinstance(overrides, dict):
        credits.update(overrides)
    return ChannelPriority(
        strategy=strategy,
        order=tuple(dict.fromkeys(c for c in order if c in channels)),
        credits={
            k: float(v)
            for k, v in credits.items()
            if k in channels and isinstance(v, int | float) and v >= 0
        },
    )


def save_priority(
    config: AppConfig,
    repo: Repository,
    *,
    strategy: str,
    order: Sequence[str],
    credits: Mapping[str, float | None],
) -> ChannelPriority:
    """校验并保存界面里的修改；credits 里的 None 表示该渠道不设额度。"""
    channels = config.models.channels
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy 应为 {' / '.join(STRATEGIES)}")
    unknown = sorted({*order, *credits} - set(channels))
    if unknown:
        raise ValueError(f"未知渠道：{unknown}")
    for name, value in credits.items():
        if value is not None and (not isinstance(value, int | float) or value < 0):
            raise ValueError(f"{name} 的每月额度应为不小于 0 的数字")
    data = {"strategy": strategy, "order": list(dict.fromkeys(order)), "credits": dict(credits)}
    repo.set_setting(SETTING_KEY, json.dumps(data, ensure_ascii=False))
    return load_priority(config, repo)


class MonthSpend:
    """spent(渠道) = 本月（UTC 自然月）该渠道的已用，缓存几十秒，避免每次调用都查库。"""

    def __init__(self, repo: Repository, now: Callable[[], datetime], ttl_s: float = 30.0) -> None:
        self._repo = repo
        self._now = now
        self._ttl = ttl_s
        self._cache: tuple[datetime, dict[str, float]] | None = None

    def all(self) -> dict[str, float]:
        now = self._now()
        if self._cache and (now - self._cache[0]).total_seconds() < self._ttl:
            return self._cache[1]
        spent = self._repo.spent_by_channel_between(*month_window(now))
        self._cache = (now, spent)
        return spent

    def __call__(self, channel: str) -> float:
        return self.all().get(channel, 0.0)

    def invalidate(self) -> None:
        self._cache = None


class WeeklyCap:
    """渠道每周 / 每月花费上限（``weekly_cap_usd`` / ``monthly_cap_usd``）：本周走这个渠道的
    花费（本机账本）加上 ``extra`` 里登记的同 key 外部花费（如 dsh 工作台），或本月走这个渠道
    的花费，到了就不再走它。只看配置，不看渠道名。"""

    def __init__(
        self,
        caps: Mapping[str, float],
        repo: Repository,
        now: Callable[[], datetime],
        *,
        rate: float = 7.2,
        unit: str = "CNY",
        ttl_s: float = 30.0,
        monthly: Mapping[str, float] | None = None,
    ) -> None:
        self.caps = {k: float(v) for k, v in caps.items()}
        self.monthly = {k: float(v) for k, v in (monthly or {}).items()}
        self._repo = repo
        self._now = now
        self._ttl = ttl_s
        self._rate, self._unit = rate, unit  # 提醒文字里按什么币种显示
        self._cache: tuple[datetime, dict[str, float]] | None = None
        self._month_cache: tuple[datetime, dict[str, float]] | None = None
        self.extra: dict[str, Callable[[], float]] = {}  # 渠道 → 本周别处花的（美元）

    @classmethod
    def from_config(cls, config: AppConfig, repo: Repository, now: Callable[[], datetime]):
        caps = {
            name: spec.weekly_cap_usd
            for name, spec in config.models.channels.items()
            if spec.weekly_cap_usd is not None
        }
        monthly = {
            name: spec.monthly_cap_usd
            for name, spec in config.models.channels.items()
            if spec.monthly_cap_usd is not None
        }
        dsh = config.roundtable.dsh
        unit = dsh.display_currency
        rate = 1.0 if unit == "USD" else dsh.usd_rates.get(unit, 1.0)
        return cls(caps, repo, now, rate=rate, unit=unit if rate != 1.0 else "USD", monthly=monthly)

    @property
    def active(self) -> bool:
        """配置里有没有设任何上限（没有就不用挡路由）。"""
        return bool(self.caps or self.monthly)

    def month_spent(self, channel: str) -> float:
        now = self._now()
        if self._month_cache and (now - self._month_cache[0]).total_seconds() < self._ttl:
            return self._month_cache[1].get(channel, 0.0)
        spent = self._repo.spent_by_channel_between(*month_window(now)) if self.monthly else {}
        self._month_cache = (now, spent)
        return spent.get(channel, 0.0)

    def _own(self) -> dict[str, float]:
        now = self._now()
        if self._cache and (now - self._cache[0]).total_seconds() < self._ttl:
            return self._cache[1]
        spent = self._repo.spent_by_channel_between(*week_window(now)) if self.caps else {}
        self._cache = (now, spent)
        return spent

    def spent(self, channel: str) -> float:
        total = self._own().get(channel, 0.0)
        if channel in self.extra:
            with contextlib.suppress(Exception):  # 外部账本读不到：只按本机账本算，不挡调用
                total += max(0.0, float(self.extra[channel]()))
        return total

    def _money(self, usd: float) -> str:
        if self._unit == "USD":
            return f"${usd:.2f}"
        return f"{usd * self._rate:.2f} {self._unit}"

    def blocked(self, channel: str) -> str | None:
        """到了本周或本月上限时返回原因；没设上限或还有余量时为 None。"""
        mcap = self.monthly.get(channel)
        if mcap is not None:
            mspent = self.month_spent(channel)
            if mspent >= mcap:
                return f"本月已花约 ${mspent:.2f}，到了每月上限 ${mcap:.2f}，下月 1 号恢复"
        cap = self.caps.get(channel)
        if cap is None:
            return None
        spent = self.spent(channel)
        if spent < cap:
            return None
        return f"本周已花约 {self._money(spent)}，到了每周上限 {self._money(cap)}，下周一恢复"

    def status(self) -> dict[str, dict[str, float | bool]]:
        out: dict[str, dict[str, float | bool]] = {}
        for ch, cap in self.caps.items():
            spent = self.spent(ch)
            out[ch] = {"spent_usd": round(spent, 4), "cap_usd": cap, "blocked": spent >= cap}
        return out

    def invalidate(self) -> None:
        self._cache = None
        self._month_cache = None
