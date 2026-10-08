"""按档位/标签筛选、随机抽取、最便宜选择。无品牌偏好：只看档位、标签、价格。"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from roundtable.core.allocation import NotEnoughModels, cheapest, draw, eligible, prefer_tags

from .conftest import model

POOL = [
    model("b1", "V1", "budget", ["math"]),
    model("b2", "V2", "budget", ["math", "vision"]),
    model("b3", "V3", "budget", ["writing"]),
    model("f1", "V1", "flagship", ["math", "vision"]),
    model("f2", "V4", "flagship", ["writing"]),
    model("u1", "V5", None),
]


def ids(models):
    return [m.id for m in models]


def test_eligible_filters():
    assert ids(eligible(POOL, tiers=["budget"])) == ["b1", "b2", "b3"]
    assert ids(eligible(POOL, tiers=["budget", "flagship"], required_tags=["vision"])) == [
        "b2",
        "f1",
    ]
    assert ids(eligible(POOL, tiers=["flagship"], exclude=["f1"])) == ["f2"]
    assert "u1" not in ids(eligible(POOL, tiers=["budget", "flagship"]))
    assert len(eligible(POOL)) == len(POOL)


def test_prefer_tags_only_when_enough():
    budget = eligible(POOL, tiers=["budget"])
    assert ids(prefer_tags(budget, ["math"], 2)) == ["b1", "b2"]
    assert ids(prefer_tags(budget, ["math"], 3)) == ["b1", "b2", "b3"]  # 不够就不筛
    assert ids(prefer_tags(budget, [], 1)) == ["b1", "b2", "b3"]


def test_draw_uniform_within_tier():
    budget = eligible(POOL, tiers=["budget"])
    counts = Counter(draw(budget, 1, random.Random(s))[0].id for s in range(3000))
    for mid in ("b1", "b2", "b3"):
        assert 850 < counts[mid] < 1150, counts


def test_draw_reproducible():
    budget = eligible(POOL, tiers=["budget"])
    a = ids(draw(budget, 2, random.Random(5)))
    assert a == ids(draw(budget, 2, random.Random(5)))


def test_draw_prefers_distinct_vendors():
    same = [model("x1", "V", "budget"), model("x2", "V", "budget"), model("y1", "W", "budget")]
    for seed in range(200):
        picked = draw(same, 2, random.Random(seed))
        assert {m.vendor for m in picked} == {"V", "W"}


def test_draw_relaxes_when_vendors_run_out():
    same = [model("x1", "V", "budget"), model("x2", "V", "budget")]
    assert sorted(ids(draw(same, 2, random.Random(0)))) == ["x1", "x2"]


def test_draw_avoids_given_vendors_when_possible():
    pool = [model("x1", "V", "budget"), model("y1", "W", "budget")]
    for seed in range(100):
        assert ids(draw(pool, 1, random.Random(seed), avoid_vendors=["V"])) == ["y1"]


def test_draw_without_vendor_preference():
    same = [model("x1", "V", "budget"), model("x2", "V", "budget"), model("y1", "W", "budget")]
    combos = {
        tuple(sorted(ids(draw(same, 2, random.Random(s), distinct_vendors=False))))
        for s in range(200)
    }
    assert ("x1", "x2") in combos


def test_draw_errors():
    with pytest.raises(NotEnoughModels):
        draw(POOL[:2], 3, random.Random(0))


def test_cheapest_by_expected_cost_and_random_ties():
    pool = [
        model("a", "A", price=(0.10, 1.00)),
        model("b", "B", price=(0.50, 0.20)),  # 输出多时更便宜
        model("c", "C", price=(0.50, 0.20)),
    ]
    assert cheapest(pool, random.Random(0), input_tokens=100, output_tokens=10).id == "a"
    picks = {
        cheapest(pool, random.Random(s), input_tokens=10, output_tokens=1000).id for s in range(50)
    }
    assert picks == {"b", "c"}
    with pytest.raises(NotEnoughModels):
        cheapest([], random.Random(0), input_tokens=1, output_tokens=1)


def test_selection_is_brand_neutral():
    """把厂商和 id 全部换名后，同一 seed 的抽取结果在位置上完全一致。"""
    renamed = [
        model(f"z{i}", f"Brand{i}", m.tier, m.tags, (m.price.input, m.price.output))
        for i, m in enumerate(POOL)
    ]
    for seed in range(50):
        a = draw(eligible(POOL, tiers=["budget"]), 2, random.Random(seed))
        b = draw(eligible(renamed, tiers=["budget"]), 2, random.Random(seed))
        assert [POOL.index(m) for m in a] == [renamed.index(m) for m in b]
