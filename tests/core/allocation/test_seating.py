"""代号分配、互评分配与乱序 —— 中立性核心，多种子重复验证。"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from roundtable.core.allocation import assign_codes, review_assignments, reviews_for, shuffled

CODES = ["甲", "乙", "丙", "丁", "戊"]
SEEDS = range(200)


def test_assign_codes_is_bijective_and_ordered_by_code():
    mapping = assign_codes(["a", "b", "c"], CODES, random.Random(1))
    assert list(mapping) == ["甲", "乙", "丙"]
    assert sorted(mapping.values()) == ["a", "b", "c"]


def test_assign_codes_reproducible_and_varies_with_seed():
    ids = ["a", "b", "c", "d"]
    assert assign_codes(ids, CODES, random.Random(7)) == assign_codes(ids, CODES, random.Random(7))
    outcomes = {tuple(assign_codes(ids, CODES, random.Random(s)).values()) for s in SEEDS}
    assert len(outcomes) > 10


def test_assign_codes_roughly_uniform():
    """每个模型拿到"甲"的概率应接近 1/n（不能固定对应某个模型）。"""
    ids = ["a", "b", "c", "d"]
    counts = Counter(assign_codes(ids, CODES, random.Random(s))["甲"] for s in range(4000))
    for mid in ids:
        assert 850 < counts[mid] < 1150, counts


@pytest.mark.parametrize("ids, codes", [(["a", "a"], CODES), (["a", "b", "c"], ["甲", "乙"])])
def test_assign_codes_errors(ids, codes):
    with pytest.raises(ValueError):
        assign_codes(ids, codes, random.Random(0))


@pytest.mark.parametrize("n", [2, 3, 4, 5])
def test_reviewer_never_reviews_self(n):
    codes = CODES[:n]
    for seed in SEEDS:
        plan = review_assignments(codes, random.Random(seed))
        for reviewer, targets in plan.items():
            assert reviewer not in targets
            assert sorted(targets) == sorted(c for c in codes if c != reviewer)


def test_review_orders_are_shuffled_independently():
    orders = Counter()
    for seed in SEEDS:
        plan = review_assignments(CODES[:4], random.Random(seed))
        orders[tuple(plan["甲"])] += 1
        # 不同评审者的顺序不应总是同一模式
    assert len(orders) == 6  # 3 个对象的全部排列都出现过


def test_review_assignments_rejects_duplicates():
    with pytest.raises(ValueError):
        review_assignments(["甲", "甲"], random.Random(0))


def test_reviews_for_excludes_self_review_even_if_present():
    reviews = {
        "甲": {"乙": "r1", "甲": "self!"},
        "乙": {"甲": "r2"},
        "丙": {"甲": "r3", "乙": "r4"},
    }
    for seed in SEEDS:
        got = reviews_for("甲", reviews, random.Random(seed))
        assert set(got) == {("乙", "r2"), ("丙", "r3")} and len(got) == 2


def test_shuffled_is_a_permutation_copy():
    items = ["甲", "乙", "丙", "丁"]
    out = shuffled(items, random.Random(3))
    assert sorted(out) == sorted(items) and items == ["甲", "乙", "丙", "丁"]
