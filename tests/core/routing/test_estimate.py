from __future__ import annotations

import pytest

from roundtable.core.config import Price
from roundtable.core.routing import (
    EstimateHistory,
    Participant,
    estimate_pipeline,
    history_from_calls,
    text_tokens,
)
from roundtable.core.routing.estimate import TokenStat

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


# --- 档位倍数、历史校准、上限 ---------------------------------------------------------


PM = P.model_copy(update={"output_multiplier": {"flagship": 2.0}, "history_min_samples": 2})


def seats(*tiers):
    return [Participant(f"m{i}", t, UNIT) for i, t in enumerate(tiers)]


def run2(members, *, history=None, caps=None, pipeline=("answer",)):
    return estimate_pipeline(
        pipeline,
        members=members,
        coordinator=Participant("c", "budget", UNIT),
        question_tokens=100,
        answer_tokens=1000,
        revise_rounds=1,
        params=PM,
        history=history,
        caps=caps,
    )


def test_flagship_output_multiplied_for_reasoning_tokens():
    e = run2(seats("flagship", "budget"))
    assert e.output_tokens == 2000 + 1000 and not e.calibrated


def test_output_clamped_to_step_cap_and_upper_bound():
    e = run2(seats("flagship"), caps={"answer": 1500})
    assert e.output_tokens == 1500
    o = PM.prompt_overhead_tokens
    assert e.max_usd == pytest.approx((o + 100 + 1500) / 1e6)
    assert e.max_usd >= e.total_usd


def test_history_replaces_formula_when_enough_samples():
    h = EstimateHistory({("m0", "answer"): TokenStat(50, 7000, 2)})
    e = run2(seats("flagship"), history=h)
    o = PM.prompt_overhead_tokens
    assert e.output_tokens == 7000 and e.input_tokens == o + 100  # 输入取较大者
    assert e.calibrated
    few = EstimateHistory({("m0", "answer"): TokenStat(50, 7000, 1)})
    assert run2(seats("flagship"), history=few).output_tokens == 2000


def test_history_retry_rate_scales_cost():
    base = run2(seats("budget"))
    h = EstimateHistory(calls_per_slot={"answer": 1.5})
    e = run2(seats("budget"), history=h)
    assert e.total_usd == pytest.approx(base.total_usd * 1.5) and e.calibrated


def _row(sid, code, step="answer", model="m0", tin=100, tout=1000, error=None):
    return {
        "session_id": sid,
        "table_no": 0,
        "step": step,
        "code": code,
        "role": "member",
        "model_id": model,
        "input_tokens": tin,
        "output_tokens": tout,
        "error": error,
    }


def test_history_from_calls_medians_and_retry_rate():
    rows = [
        _row("s1", "甲", tout=1000),
        _row("s1", "甲", tout=3000),  # 同一座位第二次调用（格式重试 / 打回重做）
        _row("s2", "甲", tout=2000),
        _row("s3", "乙", tout=5000),
        _row("s3", "丙", tout=0, error="timeout"),  # 没产出 token 的失败调用不计
    ]
    h = history_from_calls(rows, min_samples=3)
    assert h.tokens[("m0", "answer")] == TokenStat(100, 2500, 4)
    assert h.calls_per_slot["answer"] == pytest.approx(4 / 3)
    assert history_from_calls(rows, min_samples=4).calls_per_slot == {}


def test_tool_steps_get_extra_calls_until_history_known():
    base = run2(seats("budget"))
    more = estimate_pipeline(
        ("answer",),
        members=seats("budget"),
        question_tokens=100,
        answer_tokens=1000,
        revise_rounds=1,
        params=PM,
        extra_calls={"answer": 0.5},
    )
    assert more.total_usd == pytest.approx(base.total_usd * 1.5) and not more.calibrated
    known = estimate_pipeline(
        ("answer",),
        members=seats("budget"),
        question_tokens=100,
        answer_tokens=1000,
        revise_rounds=1,
        params=PM,
        extra_calls={"answer": 0.5},
        history=EstimateHistory(calls_per_slot={"answer": 1.2}),
    )
    assert known.total_usd == pytest.approx(base.total_usd * 1.2)  # 历史已包含工具轮次
