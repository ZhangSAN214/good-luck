from __future__ import annotations

import pytest

from roundtable.core.config import Price
from roundtable.core.routing import estimate_pipeline, text_tokens

from .conftest import REPO

P = REPO.routing.estimate
FULL = ["answer", "review", "revise", "synthesize", "reveal"]


def test_text_tokens_cjk_vs_latin():
    assert text_tokens("一二三四", P) == 4
    assert text_tokens("abcdefgh", P) == 2
    assert text_tokens("", P) == 0


UNIT = Price(input=1, output=1)


def run(pipeline=FULL, n=3, price=UNIT, coordinator=UNIT, **kw):
    return estimate_pipeline(
        pipeline,
        member_prices=[price] * n,
        coordinator_price=coordinator,
        question_tokens=kw.get("q", 100),
        answer_tokens=kw.get("a", 1000),
        revise_rounds=kw.get("rounds", 1),
        params=P,
    )


def test_answer_step_formula():
    e = run(["answer"], n=2)
    o = P.prompt_overhead_tokens
    assert e.input_tokens == 2 * (o + 100) and e.output_tokens == 2 * 1000
    assert e.total_usd == pytest.approx((e.input_tokens + e.output_tokens) / 1e6)


def test_more_members_cost_more():
    assert run(n=4).total_usd > run(n=3).total_usd > run(n=2).total_usd


def test_review_grows_quadratically_with_members():
    two = next(s for s in run(n=2).steps if s.step == "review").input_tokens
    four = next(s for s in run(n=4).steps if s.step == "review").input_tokens
    assert four > 2 * two


def test_revise_rounds_multiply():
    one, two = run(rounds=1), run(rounds=2)
    by = lambda e, s: next(x for x in e.steps if x.step == s).cost_usd  # noqa: E731
    assert by(two, "review") == pytest.approx(2 * by(one, "review"))
    assert by(two, "answer") == pytest.approx(by(one, "answer"))


def test_solo_pipeline_and_reveal_free():
    e = run(["answer", "reveal"], n=1, coordinator=None)
    assert [s.step for s in e.steps] == ["answer", "reveal"]
    assert e.steps[-1].cost_usd == 0


def test_unknown_steps_reported_not_charged():
    e = run(["answer", "debate"], n=2)
    assert e.unknown_steps == ("debate",)
    assert [s.step for s in e.steps] == ["answer"]


def test_flagship_prices_dominate():
    cheap = run(price=Price(input=0.1, output=0.4), coordinator=Price(input=0.1, output=0.4))
    pricey = run(price=Price(input=2, output=10), coordinator=Price(input=2, output=10))
    assert pricey.total_usd > 10 * cheap.total_usd
