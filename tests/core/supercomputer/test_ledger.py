from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.supercomputer.ledger import Ledger

T = datetime(2026, 10, 29, 23, 0, tzinfo=UTC)


def test_daily_counts_roll_over_and_persist(tmp_path):
    path = tmp_path / "super" / "ledger.json"
    led = Ledger(path)
    led.add("a", T)
    led.add("a", T)
    assert led.used("a", T) == 2
    again = Ledger(path)
    assert again.used("a", T) == 2
    assert again.used("a", T + timedelta(hours=2)) == 0  # UTC 第二天清零


def test_limits_and_cooldown(tmp_path):
    led = Ledger(tmp_path / "l.json")
    assert led.limit("a", 20) == 20
    led.set_limit("a", 5)
    assert Ledger(tmp_path / "l.json").limit("a", 20) == 5
    led.cool("a", T + timedelta(minutes=30))
    assert led.cooling("a", T) == T + timedelta(minutes=30)
    assert led.cooling("a", T + timedelta(hours=1)) is None
    led.clear_cooldown("a")
    assert led.cooling("a", T) is None


def test_memory_only_and_corrupt_file(tmp_path):
    led = Ledger(None)
    led.add("a", T)
    assert led.used("a", T) == 1
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert Ledger(bad).used("a", T) == 0
