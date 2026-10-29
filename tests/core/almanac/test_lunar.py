"""农历与节气表：公历 ↔ 农历往返、闰月、节气、超出范围。"""

from __future__ import annotations

from datetime import date, timedelta

from roundtable.core.almanac.lunar import from_lunar, solar_terms, table_years, to_lunar


def test_known_dates():
    assert to_lunar(date(2026, 2, 17))[:4] == (2026, 1, 1, False)  # 2026 春节
    assert to_lunar(date(2026, 2, 16))[:4] == (2025, 12, 29, False)  # 除夕（腊月小）
    assert to_lunar(date(2026, 9, 25))[:3] == (2026, 8, 15)  # 中秋
    assert to_lunar(date(2025, 7, 25))[:4] == (2025, 6, 1, True)  # 2025 闰六月初一
    assert from_lunar(2025, 6, 1, leap=True) == date(2025, 7, 25)
    assert from_lunar(2026, 6, 1, leap=True) is None  # 2026 没有闰六月
    assert from_lunar(2026, 2, 30) is None  # 二月小，没有三十


def test_round_trip_every_day_for_years():
    d = date(2024, 1, 1)
    while d < date(2028, 1, 1):
        lu = to_lunar(d)
        assert lu is not None
        assert from_lunar(lu.year, lu.month, lu.day, leap=lu.leap) == d
        d += timedelta(days=1)


def test_solar_terms_and_range():
    first, last = table_years()
    assert first <= 2000 and last >= 2100
    terms = solar_terms(2026)
    assert len(terms) == 24
    assert terms[date(2026, 12, 22)] == "冬至" and terms[date(2026, 2, 4)] == "立春"
    assert to_lunar(date(1890, 1, 1)) is None and solar_terms(1890) == {}
