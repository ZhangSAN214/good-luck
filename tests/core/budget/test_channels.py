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


# --- 每周上限（2026-11-04：DeepSeek 聊天 + 工作台一周合计 ¥10） ------------------


def _channel_spend(repo, channel, usd):
    from roundtable.core.providers import Completion, Message

    repo.record_call(
        repo.create_session("q", seed=1),
        step="answer",
        role="member",
        model_id="m",
        messages=[Message("user", "q")],
        completion=Completion(
            text="x",
            model_id="m",
            channel=channel,
            channel_kind="direct",
            route_model="m",
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            cost_usd=usd,
            cost_source="reported",
            latency_s=0.1,
        ),
    )


def test_weekly_cap_adds_workbench_spend_and_blocks(repo):
    from roundtable.core.budget.channels import WeeklyCap

    now = datetime.now(UTC)
    cap = WeeklyCap({"direct": 1.0}, repo, lambda: now, rate=7.2, unit="CNY", ttl_s=0)
    _channel_spend(repo, "direct", 0.6)
    _channel_spend(repo, "other", 5.0)  # 别的渠道不算
    assert cap.spent("direct") == pytest.approx(0.6)
    assert cap.blocked("direct") is None
    cap.extra["direct"] = lambda: 0.5  # 工作台花的
    assert cap.spent("direct") == pytest.approx(1.1)
    reason = cap.blocked("direct")
    assert reason and "CNY" in reason and "下周一" in reason
    assert cap.blocked("other") is None  # 没设上限


def test_weekly_cap_ignores_broken_extra_ledger(repo):
    from roundtable.core.budget.channels import WeeklyCap

    cap = WeeklyCap({"direct": 1.0}, repo, lambda: datetime.now(UTC), ttl_s=0)

    def boom():
        raise OSError("账本读不到")

    cap.extra["direct"] = boom
    assert cap.spent("direct") == 0.0 and cap.blocked("direct") is None


def test_weekly_cap_from_config_reads_channels(config, repo):
    from roundtable.core.budget.channels import WeeklyCap

    cap = WeeklyCap.from_config(config, repo, lambda: datetime.now(UTC))
    expected = {
        n: c.weekly_cap_usd
        for n, c in config.models.channels.items()
        if c.weekly_cap_usd is not None
    }
    assert cap.caps == expected


def test_monthly_cap_blocks_until_next_month(repo):
    """会员送的 API 额度每月 $100 不超（2026-11-04）：到了本月上限就不再走这个渠道。"""
    from roundtable.core.budget.channels import WeeklyCap

    cap = WeeklyCap({}, repo, lambda: datetime.now(UTC), ttl_s=0, monthly={"direct": 1.0})
    assert cap.active and cap.blocked("direct") is None
    _channel_spend(repo, "direct", 1.2)
    reason = cap.blocked("direct")
    assert reason and "每月上限" in reason and "下月 1 号" in reason
    assert cap.blocked("other") is None
    assert not WeeklyCap({}, repo, lambda: datetime.now(UTC)).active


def test_monthly_caps_from_config(config, repo):
    from roundtable.core.budget.channels import WeeklyCap

    cap = WeeklyCap.from_config(config, repo, lambda: datetime.now(UTC))
    assert cap.monthly == {
        n: c.monthly_cap_usd
        for n, c in config.models.channels.items()
        if c.monthly_cap_usd is not None
    }


def test_monthly_cap_adds_workbench_credit_spend(repo):
    """工作台用 API 额度的 Claude Code 自己报的花费也算进每月上限；外部账本坏了不挡。"""
    from roundtable.core.budget.channels import WeeklyCap

    cap = WeeklyCap({}, repo, lambda: datetime.now(UTC), ttl_s=0, monthly={"direct": 1.0})
    cap.monthly_extra["direct"] = lambda: 0.6
    _channel_spend(repo, "direct", 0.5)
    assert cap.month_spent("direct") == pytest.approx(1.1)
    assert "每月上限" in (cap.blocked("direct") or "")

    def broken():
        raise OSError("账本读不到")

    cap.monthly_extra["direct"] = broken
    assert cap.month_spent("direct") == pytest.approx(0.5)
    assert cap.blocked("direct") is None
