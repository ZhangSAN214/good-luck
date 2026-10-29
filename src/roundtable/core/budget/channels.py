"""渠道优先顺序与每月额度：读配置 + 界面里的修改（app_settings），本月各渠道已用按本机账本算。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from roundtable.core.config import AppConfig
from roundtable.core.providers.priority import STRATEGIES, ChannelPriority
from roundtable.core.storage import Repository

from .guard import month_window

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
