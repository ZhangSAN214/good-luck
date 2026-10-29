from __future__ import annotations

from datetime import UTC, datetime

import pytest

from roundtable.core.dsh.usage import UsageLedger

T0 = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)


def at(day: int, month: int = 10) -> datetime:
    return datetime(2026, month, day, 8, 0, tzinfo=UTC)


def test_counts_drops_while_running(tmp_path):
    led = UsageLedger(tmp_path / "u.json")
    led.rebase(10.0, at(1), "USD", 10.0)
    assert led.observe(9.5, at(2), "USD", 9.5) == 0.5
    assert led.observe(9.6, at(3), "USD", 9.6) == 0.0  # 充值：不算花费
    assert led.observe(9.0, at(4), "USD", 9.0, shared_usd=0.2) == pytest.approx(
        0.4
    )  # 扣掉圆桌自己用掉的
    assert round(led.spent(at(4)), 6) == 0.9
    # 停着的时候余额变少不算：重新启动时以当前余额为起点
    led.rebase(5.0, at(5), "USD", 5.0)
    assert round(led.spent(at(5)), 6) == 0.9
    # 账本存在本机文件，重开还在
    assert round(UsageLedger(tmp_path / "u.json").spent(at(5)), 6) == 0.9


def test_new_month_starts_from_zero(tmp_path):
    led = UsageLedger(tmp_path / "u.json")
    led.rebase(10.0, at(30), "USD", 10.0)
    led.observe(9.0, at(31), "USD", 9.0)
    assert led.observe(8.5, at(1, 11), "USD", 8.5) == 0.5
    assert led.spent(at(1, 11)) == 0.5
    assert led.state.history["2026-10"] == 1.0


def test_broken_ledger_file_starts_over(tmp_path):
    p = tmp_path / "u.json"
    p.write_text("{not json", encoding="utf-8")
    assert UsageLedger(p).spent(T0) == 0.0
    assert UsageLedger(None).spent(T0) == 0.0
