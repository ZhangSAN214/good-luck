from __future__ import annotations

import pytest

from roundtable.core.budget.usage import UsageLedger
from roundtable.core.providers import Attempt, Completion, ErrorKind


def completion(channel, model_id="m", cost=0.1, attempts=None):
    return Completion(
        text="x",
        model_id=model_id,
        channel=channel,
        channel_kind="direct",
        route_model=f"{channel}/{model_id}",
        input_tokens=100,
        output_tokens=50,
        cached_tokens=10,
        cost_usd=cost,
        cost_source="estimated",
        latency_s=1.0,
        attempts=tuple(attempts or [Attempt(channel, True, 1.0)]),
    )


def test_totals_split_by_channel():
    ledger = UsageLedger()
    ledger.record(completion("google", cost=0.1))
    ledger.record(completion("google", cost=0.2))
    ledger.record(
        completion(
            "openrouter",
            cost=0.4,
            attempts=[
                Attempt("google", False, 0.2, ErrorKind.RATE_LIMIT),
                Attempt("openrouter", True, 1),
            ],
        )
    )
    by = ledger.by_channel()
    assert by["google"].calls == 2 and by["google"].cost_usd == pytest.approx(0.3)
    assert by["google"].failed_attempts == 1
    assert by["openrouter"].calls == 1 and by["openrouter"].cost_usd == pytest.approx(0.4)
    total = ledger.total()
    assert (total.calls, total.failed_attempts) == (3, 1)
    assert total.cost_usd == pytest.approx(0.7)
    assert (total.input_tokens, total.output_tokens, total.cached_tokens) == (300, 150, 30)


def test_totals_by_model():
    ledger = UsageLedger()
    ledger.record(completion("google", model_id="a"))
    ledger.record(completion("openrouter", model_id="a"))
    ledger.record(completion("openrouter", model_id="b"))
    assert ledger.by_model()["a"].calls == 2 and ledger.by_model()["b"].calls == 1


def test_failures_without_success():
    ledger = UsageLedger()
    ledger.record_failures(
        [
            Attempt("google", False, 0.1, ErrorKind.QUOTA),
            Attempt("openrouter", False, 0.1, ErrorKind.NETWORK),
        ]
    )
    assert ledger.by_channel()["google"].failed_attempts == 1
    assert ledger.total().calls == 0
