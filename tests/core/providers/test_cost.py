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


def test_image_cost_prefers_unit_price_then_usage_then_typical_tokens():
    from roundtable.core.config import ModelSpec
    from roundtable.core.providers import image_cost

    def model(**kw):
        return ModelSpec.model_validate(
            {
                "id": "m",
                "vendor": "V",
                "price": {"input": 2.0, "output": 10.0},
                "routes": [{"channel": "c", "model": "m"}],
                **kw,
            }
        )

    per_image = model(media_price={"unit": "image", "usd": 0.04})
    assert image_cost(per_image, None, 2, 0, 5000) == pytest.approx(0.08)  # 每张单价优先
    by_token = model(image_tokens=1000)
    typical = (300 * 2.0 + 1000 * 10.0) / 1e6  # 没有用量：典型 token 数 × 图像输出价
    assert image_cost(by_token, None, 1, 0, 0) == pytest.approx(typical)
    assert image_cost(by_token, None, 3, 0, 0) == pytest.approx((300 * 2.0 + 3000 * 10.0) / 1e6)
    used = (100 * 2.0 + 1500 * 10.0) / 1e6  # 渠道返回了用量：按用量
    assert image_cost(by_token, None, 1, 100, 1500) == pytest.approx(used)
