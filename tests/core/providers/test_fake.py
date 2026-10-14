from __future__ import annotations

import pytest

from roundtable.core.providers import ErrorKind, FakeProvider, Message, ProviderError, RawCompletion

MSG = [Message("user", "hello")]


async def test_default_echo_and_call_log():
    fake = FakeProvider("ch")
    raw = await fake.complete("m", MSG, {"t": 1})
    assert raw.text == "[ch:m] hello"
    assert raw.input_tokens > 0 and raw.output_tokens > 0
    assert fake.calls[0].model == "m" and fake.calls[0].params == {"t": 1}


async def test_script_order_then_default():
    fake = FakeProvider("ch", script={"m": ["one", ErrorKind.RATE_LIMIT]}, default="dflt")
    assert (await fake.complete("m", MSG, {})).text == "one"
    with pytest.raises(ProviderError) as info:
        await fake.complete("m", MSG, {})
    assert info.value.kind == ErrorKind.RATE_LIMIT and info.value.channel == "ch"
    assert (await fake.complete("m", MSG, {})).text == "dflt"


async def test_callable_and_raw_outcomes():
    fake = FakeProvider("ch")
    fake.queue("m", lambda model, msgs: f"{model}:{len(msgs)}", RawCompletion("r", 1, 2, 0, 0.1))
    assert (await fake.complete("m", MSG, {})).text == "m:1"
    raw = await fake.complete("m", MSG, {})
    assert raw.reported_cost_usd == 0.1


async def test_reported_cost():
    raw = await FakeProvider("ch", reported_cost_usd=0.25).complete("m", MSG, {})
    assert raw.reported_cost_usd == 0.25
