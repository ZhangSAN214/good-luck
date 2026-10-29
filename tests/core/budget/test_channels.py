"""渠道优先顺序的读写：配置给默认值，界面保存的覆盖；本月各渠道已用来自本机账本。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from roundtable.core.budget.channels import SETTING_KEY, MonthSpend, load_priority, save_priority
from roundtable.core.config import load_config
from roundtable.core.storage import Repository, connect, iso

from .test_guard import Clock, spend


@pytest.fixture
def config():
    return load_config()


@pytest.fixture
def repo():
    return Repository(connect())


def test_defaults_from_config(config, repo):
    prio = load_priority(config, repo)
    assert prio.strategy == config.roundtable.channel_priority.strategy
    assert prio.credits == {
        n: c.monthly_credit_usd
        for n, c in config.models.channels.items()
        if c.monthly_credit_usd is not None
    }


def test_save_and_reload(config, repo):
    first, second = list(config.models.channels)[:2]
    prio = save_priority(
        config, repo, strategy="custom", order=[second, first, second], credits={first: 100}
    )
    assert prio.strategy == "custom" and prio.order == (second, first)
    assert prio.credits[first] == 100.0
    assert load_priority(config, repo) == prio
    # None = 不设额度
    prio = save_priority(config, repo, strategy="auto", order=[], credits={first: None})
    assert first not in prio.credits and prio.strategy == "auto"


@pytest.mark.parametrize(
    "kw",
    [
        {"strategy": "fastest", "order": [], "credits": {}},
        {"strategy": "auto", "order": ["nope"], "credits": {}},
        {"strategy": "auto", "order": [], "credits": {"nope": 1}},
    ],
)
def test_save_rejects_bad_input(config, repo, kw):
    with pytest.raises(ValueError):
        save_priority(config, repo, **kw)


def test_broken_setting_falls_back(config, repo):
    repo.set_setting(SETTING_KEY, "{not json")
    assert load_priority(config, repo).strategy == config.roundtable.channel_priority.strategy
    repo.set_setting(SETTING_KEY, '{"strategy": "x", "order": ["gone"], "credits": {"gone": 1}}')
    prio = load_priority(config, repo)
    assert prio.order == () and "gone" not in prio.credits


def test_month_spend_counts_this_month_only_and_caches():
    clock = Clock(datetime(2026, 10, 31, 23, tzinfo=UTC))
    repo = Repository(connect(), clock=lambda: iso(clock.now))
    session = repo.create_session("q", seed=1)
    spend(repo, session, 2.0)
    spent = MonthSpend(repo, clock, ttl_s=30)
    assert spent("c") == 2.0 and spent("other") == 0.0
    spend(repo, session, 1.0)
    assert spent("c") == 2.0  # 缓存中
    spent.invalidate()
    assert spent("c") == 3.0
    clock.now = datetime(2026, 11, 1, 0, 1, tzinfo=UTC)  # 新的月份
    assert spent("c") == 0.0
