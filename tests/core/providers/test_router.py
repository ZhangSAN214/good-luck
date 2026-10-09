"""渠道路由：模式筛选、key 缺失跳过、出错切换、冷却、多轮重试。全部使用 Fake provider。"""

from __future__ import annotations

import pytest

from roundtable.core.config.schema import RequestPolicy
from roundtable.core.providers import (
    AllChannelsFailed,
    ChannelRouter,
    ErrorKind,
    FakeProvider,
    KeyRing,
    Message,
    NoChannelAvailable,
    RawCompletion,
    build_providers,
)

from .conftest import models_config

MSG = [Message("user", "hi")]
CHANNELS = {"google": "direct", "anthropic": "direct", "openrouter": "aggregator"}
MODELS = {
    "gemini": ["google", "openrouter"],
    "claude": ["anthropic", "openrouter"],
    "qwen": ["openrouter"],
}


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Sleeper:
    def __init__(self):
        self.calls = []

    async def __call__(self, seconds):
        self.calls.append(seconds)


def make_router(mode="auto", providers=None, policy=None, unavailable=None, **kw):
    cfg = models_config(CHANNELS, MODELS)
    providers = providers or {name: FakeProvider(name) for name in CHANNELS}
    clock, sleeper = Clock(), Sleeper()
    router = ChannelRouter(
        cfg,
        providers,
        mode=mode,
        policy=policy or RequestPolicy(failover_rounds=1, backoff_s=1),
        unavailable=unavailable,
        clock=clock,
        sleep=sleeper,
    )
    return router, providers, clock, sleeper


# --- 正常路径 -------------------------------------------------------------------


async def test_first_channel_used_when_healthy():
    router, p, *_ = make_router()
    c = await router.complete("gemini", MSG)
    assert (c.channel, c.channel_kind, c.route_model) == ("google", "direct", "google/gemini")
    assert not c.failed_over and len(c.attempts) == 1
    assert p["openrouter"].calls == []


async def test_params_merge_model_route_and_call():
    cfg = models_config(CHANNELS, {"gemini": ["google"]})
    data = cfg.model_dump()
    data["models"][0]["params"] = {"temperature": 0.2, "max_tokens": 100}
    data["models"][0]["routes"][0]["params"] = {"max_tokens": 200}
    cfg = type(cfg).model_validate(data)
    fake = FakeProvider("google")
    router = ChannelRouter(cfg, {"google": fake})
    await router.complete("gemini", MSG, {"temperature": 0.5})
    assert fake.calls[0].params == {"temperature": 0.5, "max_tokens": 200}


# --- 切换 -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    [
        ErrorKind.RATE_LIMIT,
        ErrorKind.QUOTA,
        ErrorKind.NETWORK,
        ErrorKind.TIMEOUT,
        ErrorKind.SERVER,
        ErrorKind.AUTH,
        ErrorKind.NOT_FOUND,
    ],
)
async def test_failover_to_next_channel(kind):
    router, p, *_ = make_router()
    p["google"].queue("google/gemini", kind)
    c = await router.complete("gemini", MSG)
    assert c.channel == "openrouter"
    assert c.failed_over
    assert [(a.channel, a.ok, a.error) for a in c.attempts] == [
        ("google", False, kind),
        ("openrouter", True, None),
    ]


@pytest.mark.parametrize("kind", [ErrorKind.BAD_REQUEST, ErrorKind.REFUSAL])
async def test_no_failover_for_request_problems(kind):
    router, p, *_ = make_router()
    p["google"].queue("google/gemini", kind)
    with pytest.raises(AllChannelsFailed) as info:
        await router.complete("gemini", MSG)
    assert info.value.last.kind == kind
    assert p["openrouter"].calls == []


async def test_all_channels_fail():
    router, p, *_ = make_router()
    p["google"].queue("google/gemini", ErrorKind.RATE_LIMIT)
    p["openrouter"].queue("openrouter/gemini", ErrorKind.QUOTA)
    with pytest.raises(AllChannelsFailed) as info:
        await router.complete("gemini", MSG)
    err = info.value
    assert [a.channel for a in err.attempts] == ["google", "openrouter"]
    assert err.last.kind == ErrorKind.QUOTA
    assert "google(rate_limit) → openrouter(quota)" in str(err)


async def test_retry_rounds_with_backoff():
    policy = RequestPolicy(failover_rounds=3, backoff_s=2)
    router, p, _, sleeper = make_router(policy=policy)
    p["google"].queue("google/gemini", ErrorKind.NETWORK, ErrorKind.NETWORK)
    p["openrouter"].queue("openrouter/gemini", ErrorKind.SERVER, ErrorKind.SERVER)
    c = await router.complete("gemini", MSG)
    assert sleeper.calls == [2, 4]
    assert len(c.attempts) == 5 and c.attempts[-1].ok


async def test_permanent_errors_not_retried_in_later_rounds():
    policy = RequestPolicy(failover_rounds=3, backoff_s=0)
    router, p, *_ = make_router(policy=policy)
    p["google"].queue("google/gemini", ErrorKind.AUTH)
    p["openrouter"].queue("openrouter/gemini", ErrorKind.RATE_LIMIT)
    c = await router.complete("gemini", MSG)
    assert c.channel == "openrouter"
    assert len(p["google"].calls) == 1  # key 无效的渠道不会再试


async def test_gives_up_when_every_channel_is_permanent():
    policy = RequestPolicy(failover_rounds=5, backoff_s=1)
    router, p, _, sleeper = make_router(policy=policy)
    p["google"].queue("google/gemini", ErrorKind.QUOTA)
    p["openrouter"].queue("openrouter/gemini", ErrorKind.AUTH)
    with pytest.raises(AllChannelsFailed):
        await router.complete("gemini", MSG)
    assert sleeper.calls == []  # 没有可重试的渠道，不空等


# --- 冷却 -----------------------------------------------------------------------


async def test_cooled_down_channel_tried_last_then_recovers():
    policy = RequestPolicy(failover_rounds=1, cooldown_rate_limit_s=30)
    router, p, clock, _ = make_router(policy=policy)
    p["google"].queue("google/gemini", ErrorKind.RATE_LIMIT)
    await router.complete("gemini", MSG)

    # 冷却期内：直接先走 openrouter
    c = await router.complete("gemini", MSG)
    assert c.channel == "openrouter" and len(c.attempts) == 1

    # 冷却结束：恢复配置顺序
    clock.now += 31
    c = await router.complete("gemini", MSG)
    assert c.channel == "google"


async def test_retry_after_extends_cooldown():
    from roundtable.core.providers import ProviderError

    policy = RequestPolicy(failover_rounds=1, cooldown_rate_limit_s=10)
    router, p, clock, _ = make_router(policy=policy)

    def limited(model, messages):
        raise ProviderError(ErrorKind.RATE_LIMIT, "google", retry_after=120)

    p["google"].queue("google/gemini", limited)
    await router.complete("gemini", MSG)
    clock.now += 60
    assert (await router.complete("gemini", MSG)).channel == "openrouter"
    clock.now += 61
    assert (await router.complete("gemini", MSG)).channel == "google"


async def test_quota_cooldown_is_long():
    policy = RequestPolicy(failover_rounds=1, cooldown_quota_s=600)
    router, p, clock, _ = make_router(policy=policy)
    p["google"].queue("google/gemini", ErrorKind.QUOTA)
    await router.complete("gemini", MSG)
    clock.now += 300
    assert (await router.complete("gemini", MSG)).channel == "openrouter"


# --- 模式 -----------------------------------------------------------------------


async def test_mode_openrouter_only_uses_aggregator():
    router, p, *_ = make_router(mode="openrouter")
    c = await router.complete("claude", MSG)
    assert c.channel == "openrouter"
    assert p["anthropic"].calls == []
    assert router.plan("claude").skipped == {"anthropic": "渠道模式不包含 direct"}


async def test_mode_direct_never_uses_aggregator():
    router, p, *_ = make_router(mode="direct")
    p["anthropic"].queue("anthropic/claude", ErrorKind.RATE_LIMIT)
    with pytest.raises(AllChannelsFailed):
        await router.complete("claude", MSG)
    assert p["openrouter"].calls == []


def test_mode_direct_excludes_aggregator_only_models():
    router, *_ = make_router(mode="direct")
    assert [m.id for m in router.available_models()] == ["gemini", "claude"]
    assert router.plan("qwen").usable == ()
    assert "aggregator" in str(NoChannelAvailable("qwen", router.plan("qwen").skipped))


async def test_no_channel_available_raises_before_calling():
    router, p, *_ = make_router(mode="direct")
    with pytest.raises(NoChannelAvailable):
        await router.complete("qwen", MSG)
    assert all(fake.calls == [] for fake in p.values())


# --- key 缺失 -------------------------------------------------------------------


def test_missing_keys_skip_channels():
    cfg = models_config(CHANNELS, MODELS)
    keys = KeyRing.from_env(
        [c.key_env for c in cfg.channels.values()],
        environ={"OPENROUTER_API_KEY": "placeholder-or-123"},
    )
    providers, unavailable = build_providers(cfg, keys, 30)
    assert sorted(providers) == ["openrouter"]
    assert unavailable == {"google": "缺少 key", "anthropic": "缺少 key"}

    router = ChannelRouter(cfg, providers, unavailable=unavailable)
    plan = router.plan("claude")
    assert [r.channel for r in plan.usable] == ["openrouter"]
    assert plan.skipped == {"anthropic": "缺少 key"}


async def test_missing_key_falls_through_to_next_channel():
    cfg = models_config(CHANNELS, MODELS)
    keys = KeyRing.from_env(
        [c.key_env for c in cfg.channels.values()], environ={"GOOGLE_API_KEY": "placeholder-g-123"}
    )
    providers, unavailable = build_providers(cfg, keys, 30)
    router = ChannelRouter(cfg, providers, unavailable=unavailable)
    assert (await router.complete("gemini", MSG)).channel == "google"
    assert [m.id for m in router.available_models()] == ["gemini"]
    assert router.unavailable_models() == {
        "claude": {"anthropic": "缺少 key", "openrouter": "缺少 key"},
        "qwen": {"openrouter": "缺少 key"},
    }


def test_no_keys_at_all_means_no_models():
    cfg = models_config(CHANNELS, MODELS)
    providers, unavailable = build_providers(cfg, KeyRing({}), 30)
    router = ChannelRouter(cfg, providers, unavailable=unavailable)
    assert providers == {} and router.available_models() == []


def test_disabled_models_not_available():
    cfg = models_config(CHANNELS, MODELS)
    data = cfg.model_dump()
    data["models"][0]["enabled"] = False
    cfg = type(cfg).model_validate(data)
    router = ChannelRouter(cfg, {n: FakeProvider(n) for n in CHANNELS})
    assert "gemini" not in [m.id for m in router.available_models()]


# --- 费用 -----------------------------------------------------------------------


async def test_reported_cost_preferred():
    providers = {n: FakeProvider(n, reported_cost_usd=0.5) for n in CHANNELS}
    router, *_ = make_router(providers=providers)
    c = await router.complete("gemini", MSG)
    assert (c.cost_usd, c.cost_source) == (0.5, "reported")


async def test_estimated_cost_from_route_price():
    router, p, *_ = make_router()
    p["google"].queue("google/gemini", RawCompletion("x", 1_000_000, 1_000_000))
    c = await router.complete("gemini", MSG)
    # 价格 (1, 2) 美元 / 百万 token
    assert (c.cost_usd, c.cost_source) == (pytest.approx(3.0), "estimated")


async def test_aclose_closes_all():
    closed = []

    class Closing(FakeProvider):
        async def aclose(self):
            closed.append(self.channel)

    router, *_ = make_router(providers={n: Closing(n) for n in CHANNELS})
    await router.aclose()
    assert sorted(closed) == sorted(CHANNELS)


# --- 限速与并发 -------------------------------------------------------------------


async def test_concurrency_is_capped_per_channel():
    import asyncio

    class Slow(FakeProvider):
        def __init__(self, name):
            super().__init__(name)
            self.now = self.peak = 0

        async def complete(self, model, messages, params):
            self.now += 1
            self.peak = max(self.peak, self.now)
            await asyncio.sleep(0.01)
            self.now -= 1
            return await super().complete(model, messages, params)

    slow = Slow("openrouter")
    router, *_ = make_router(
        providers={"openrouter": slow}, policy=RequestPolicy(failover_rounds=1, max_concurrent=3)
    )
    await asyncio.gather(*(router.complete("qwen", MSG) for _ in range(12)))
    assert slow.peak == 3 and len(slow.calls) == 12


async def test_requests_per_minute_queues_instead_of_failing():
    router, providers, clock, sleeper = make_router(
        providers={"openrouter": FakeProvider("openrouter")},
        policy=RequestPolicy(failover_rounds=1, requests_per_minute=2),
    )

    async def advancing(seconds):  # 等待会让时间前进（否则限速循环永远等不到）
        sleeper.calls.append(seconds)
        clock.now += seconds

    router._sleep = advancing  # noqa: SLF001
    for _ in range(2):
        await router.complete("qwen", MSG)
    assert sleeper.calls == []
    clock.now += 20
    await router.complete("qwen", MSG)  # 第 3 次：最早的一次是 20 秒前，要等满 60 秒
    assert sleeper.calls == [pytest.approx(40)]
    assert len(providers["openrouter"].calls) == 3


async def test_limits_do_not_apply_across_channels():
    router, providers, clock, sleeper = make_router(
        policy=RequestPolicy(failover_rounds=1, requests_per_minute=1)
    )
    await router.complete("gemini", MSG)  # google
    await router.complete("claude", MSG)  # anthropic：各渠道分别计
    assert sleeper.calls == []
