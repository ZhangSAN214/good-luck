"""渠道优先顺序：自动按配置、自定义按用户顺序；每月额度没用完排最前、用完排最后；冷却仍排最后。"""

from __future__ import annotations

from roundtable.core.config import Route
from roundtable.core.providers import ChannelRouter, FakeProvider, Message
from roundtable.core.providers.priority import ChannelPriority, credit_state, order_routes

from .conftest import models_config

ROUTES = [Route(channel=c, model="m") for c in ("anthropic", "google", "openrouter")]


def names(routes):
    return [r.channel for r in routes]


def no_spend(_channel):
    return 0.0


def test_auto_keeps_config_order():
    assert names(order_routes(ROUTES, ChannelPriority(), no_spend)) == [
        "anthropic",
        "google",
        "openrouter",
    ]


def test_custom_order_then_unlisted_in_config_order():
    prio = ChannelPriority("custom", ("openrouter",))
    assert names(order_routes(ROUTES, prio, no_spend)) == ["openrouter", "anthropic", "google"]


def test_order_ignored_in_auto():
    prio = ChannelPriority("auto", ("openrouter",))
    assert names(order_routes(ROUTES, prio, no_spend))[0] == "anthropic"


def test_credit_left_goes_first_even_over_custom_order():
    spent = {"google": 3.0}
    prio = ChannelPriority("custom", ("openrouter", "anthropic"), {"google": 10.0})
    assert names(order_routes(ROUTES, prio, spent.get)) == ["google", "openrouter", "anthropic"]


def test_used_up_credit_goes_last():
    spent = {"anthropic": 10.0}
    prio = ChannelPriority("auto", (), {"anthropic": 10.0})
    assert names(order_routes(ROUTES, prio, lambda c: spent.get(c, 0.0))) == [
        "google",
        "openrouter",
        "anthropic",
    ]


def test_credit_state():
    assert credit_state(None, 5) == "none"
    assert credit_state(5, 4.99) == "left"
    assert credit_state(5, 5) == "used"
    assert credit_state(0, 0) == "used"


async def test_router_follows_priority_and_spend():
    cfg = models_config(
        {"anthropic": "direct", "openrouter": "aggregator"}, {"claude": ["openrouter", "anthropic"]}
    )
    fakes = {n: FakeProvider(n) for n in ("anthropic", "openrouter")}
    router = ChannelRouter(cfg, fakes)
    msg = [Message("user", "hi")]
    assert (await router.complete("claude", msg)).channel == "openrouter"

    spent = {"anthropic": 0.0}
    router.set_priority(ChannelPriority(credits={"anthropic": 5.0}), lambda c: spent.get(c, 0.0))
    assert (await router.complete("claude", msg)).channel == "anthropic"

    spent["anthropic"] = 5.0  # 本月额度用完 → 回到中转站
    assert (await router.complete("claude", msg)).channel == "openrouter"

    router.set_priority(ChannelPriority("custom", ("anthropic",)))
    assert router.priority.strategy == "custom"
    assert (await router.complete("claude", msg)).channel == "anthropic"
