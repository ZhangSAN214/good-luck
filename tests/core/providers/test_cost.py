from __future__ import annotations

import pytest

from roundtable.core.config import Price
from roundtable.core.providers import estimate_cost


def test_basic_cost():
    assert estimate_cost(Price(input=2, output=10), 1_000_000, 100_000) == pytest.approx(3.0)


def test_cached_tokens_use_cached_price():
    price = Price(input=2, output=10, cached_input=0.2)
    # 1M 输入中 0.5M 命中缓存：0.5*2 + 0.5*0.2 = 1.1
    assert estimate_cost(price, 1_000_000, 0, 500_000) == pytest.approx(1.1)


def test_cached_without_cached_price_is_conservative():
    assert estimate_cost(Price(input=2, output=10), 1_000_000, 0, 500_000) == pytest.approx(2.0)


def test_cached_clamped_to_input():
    price = Price(input=2, output=10, cached_input=0)
    assert estimate_cost(price, 100, 0, 1_000) == 0
