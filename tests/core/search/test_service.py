from __future__ import annotations

import pytest

from roundtable.core.config import load_config
from roundtable.core.config.schema import SearchPrice, SearchProviderSpec
from roundtable.core.providers import ErrorKind, KeyRing
from roundtable.core.search import FakeSearch, SearchService, SearchUnavailable


def fake(name, per_search=0.01):
    spec = SearchProviderSpec(
        adapter="fake",
        base_url="https://x.test",
        price=SearchPrice(per_search=per_search, per_fetch=0.002),
    )
    return FakeSearch(name, spec)


async def test_failover_to_next_provider_and_cost():
    first, second = fake("a"), fake("b", per_search=0.02)
    first.errors.append(ErrorKind.RATE_LIMIT)
    service = SearchService({"a": first, "b": second})
    response, call = await service.search("q", 3)
    assert call.provider == "b" and call.cost_usd == 0.02 and call.cost_source == "estimated"
    assert [(a.channel, a.ok) for a in call.attempts] == [("a", False), ("b", True)]
    assert len(response.hits) == 3
    _, call = await service.fetch(["https://x/1", "https://x/2"], 100)
    assert call.cost_usd == pytest.approx(0.004)


async def test_non_failover_error_stops():
    first, second = fake("a"), fake("b")
    first.errors.append(ErrorKind.BAD_REQUEST)
    with pytest.raises(SearchUnavailable):
        await SearchService({"a": first, "b": second}).search("q", 3)
    assert second.queries == []


async def test_no_key_means_unavailable():
    models = load_config().models
    service = SearchService.build(models, KeyRing({}), 10)
    assert not service.available and "没有 key" in service.reason()
    with pytest.raises(SearchUnavailable):
        await service.search("q", 3)
    assert "search_providers" in SearchService({}).reason()
