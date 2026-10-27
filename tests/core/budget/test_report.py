from __future__ import annotations

from datetime import date

import pytest

from roundtable.core.budget import report as r


def row(**kw):
    base = {
        "day": "2026-10-20",
        "workflow": "discussion",
        "step": "answer",
        "model_id": "m1",
        "channel": "c",
        "hidden": False,
        "meeting": False,
        "calls": 1,
        "failed": 0,
        "input_tokens": 100,
        "output_tokens": 50,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.01,
    }
    return {**base, **kw}


LIFE = ("life", "memory")


@pytest.mark.parametrize(
    ("kw", "feature"),
    [
        ({}, "work"),
        ({"workflow": "brainstorm"}, "work"),
        ({"workflow": "office"}, "office"),
        ({"workflow": "chat", "step": "chat"}, "chat"),
        ({"workflow": "chat", "step": "life"}, "life"),
        ({"workflow": "evolution_scout"}, "evolution"),
        ({"workflow": "discussion", "meeting": True}, "evolution"),
    ],
)
def test_feature_of(kw, feature):
    assert r.feature_of(row(**kw), LIFE) == feature


def test_summarize_hides_unrevealed_from_members():
    rows = [
        row(cost_usd=0.02),
        row(model_id="m2", cost_usd=0.05, workflow="office"),
        row(model_id="m1", hidden=True, cost_usd=0.1),
    ]
    rep = r.summarize(rows, LIFE)
    assert rep.totals.calls == 3 and rep.totals.cost_usd == pytest.approx(0.17)
    assert list(rep.by_model) == ["m2", "m1"]  # 按花费从高到低
    assert rep.by_model["m1"].cost_usd == pytest.approx(0.02)  # 匿名那笔不算在成员名下
    assert rep.hidden.cost_usd == pytest.approx(0.1)
    assert list(rep.by_feature) == ["work", "office"]
    assert rep.by_channel["c"].calls == 3


def test_daily_fills_empty_days():
    rows = [row(day="2026-10-19", cost_usd=0.5), row(day="2026-10-01", cost_usd=9)]
    days = r.daily(rows, date(2026, 10, 20), 3, LIFE)
    assert [d["day"] for d in days] == ["2026-10-18", "2026-10-19", "2026-10-20"]
    assert [d["cost_usd"] for d in days] == [0, 0.5, 0]
    assert days[1]["features"] == {"work": 0.5}


def test_token_equivalents_and_tier_average():
    prices = [
        r.PriceRow("a", "A", "budget", 1.0, 4.0),
        r.PriceRow("b", "B", "budget", 3.0, 8.0),
        r.PriceRow("free", "F", None, 0.0, 0.0),
    ]
    tiers = r.tier_averages(prices, lambda t: {"budget": "节电"}[t])
    assert tiers == [r.PriceRow("tier:budget", "节电平均", "budget", 2.0, 6.0)]
    rows = r.token_equivalents(2.0, [*tiers, *prices], share=0.25)
    avg, a, _, free = rows
    assert (avg["input_tokens"], avg["output_tokens"]) == (1_000_000, 333_333)
    assert a["blended_tokens"] == int(2.0 / (0.75 * 1 + 0.25 * 4) * 1_000_000)
    assert free["input_tokens"] is None and free["blended_tokens"] is None
    assert r.token_equivalents(1.0, prices[:1], None)[0]["blended_tokens"] is None


def test_output_share():
    assert r.output_share(r.Totals()) is None
    t = r.Totals(input_tokens=300, output_tokens=100)
    assert r.output_share(t) == 0.25
