"""Tavily 适配器：用 httpx.MockTransport 模拟服务端，不联网。"""

from __future__ import annotations

import json

import httpx
import pytest

from roundtable.core.config.schema import SearchProviderSpec
from roundtable.core.providers import ErrorKind, ProviderError, Secret
from roundtable.core.search.tavily import TavilySearch

KEY = "tvly-" + "k" * 32
SPEC = SearchProviderSpec(
    adapter="tavily",
    base_url="https://tavily.example.test",
    key_env="TAVILY_API_KEY",
    params={"search_depth": "basic"},
)


def make(handler):
    return TavilySearch("tavily", SPEC, Secret(KEY), 30, transport=httpx.MockTransport(handler))


async def test_search_request_and_parse():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T1", "url": "https://a.test/1", "content": "c1", "score": 0.9},
                    {"title": "bad", "url": "javascript:alert(1)", "content": "x"},
                    {"url": "https://b.test/2", "content": "c2"},
                ]
            },
        )

    out = await make(handler).search("极值 判定", 5)
    assert seen["url"] == "https://tavily.example.test/search"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"] == {"search_depth": "basic", "query": "极值 判定", "max_results": 5}
    assert KEY not in json.dumps(seen["body"])  # key 只在请求头
    assert [(h.title, h.url) for h in out.hits] == [
        ("T1", "https://a.test/1"),
        ("https://b.test/2", "https://b.test/2"),
    ]


async def test_extract_and_failed_urls():
    def handler(request: httpx.Request):
        body = json.loads(request.content)
        assert request.url.path == "/extract" and body["urls"] == [
            "https://a.test/1",
            "https://b.test",
        ]
        return httpx.Response(
            200,
            json={
                "results": [{"url": "https://a.test/1", "raw_content": "x" * 100}],
                "failed_results": [{"url": "https://b.test", "error": "timeout"}],
            },
        )

    out = await make(handler).fetch(["https://a.test/1", "https://b.test"], 10)
    assert out.pages[0].text == "x" * 10 and out.pages[0].ok and not out.pages[1].ok


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, ErrorKind.AUTH),
        (429, ErrorKind.RATE_LIMIT),
        (432, ErrorKind.QUOTA),
        (433, ErrorKind.QUOTA),
        (500, ErrorKind.SERVER),
        (400, ErrorKind.BAD_REQUEST),
    ],
)
async def test_errors_classified_and_key_redacted(status, kind):
    def handler(request):
        return httpx.Response(status, json={"detail": {"error": f"bad key {KEY}"}})

    with pytest.raises(ProviderError) as info:
        await make(handler).search("q", 3)
    assert info.value.kind == kind
    assert KEY not in str(info.value) and info.value.__cause__ is None


async def test_network_errors():
    def timeout(request):
        raise httpx.ReadTimeout("slow")

    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(ProviderError) as info:
        await make(timeout).search("q", 3)
    assert info.value.kind == ErrorKind.TIMEOUT
    with pytest.raises(ProviderError) as info:
        await make(down).search("q", 3)
    assert info.value.kind == ErrorKind.NETWORK
