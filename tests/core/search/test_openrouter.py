"""OpenRouter 联网搜索适配器：请求格式（plugin / server_tool）、url_citation 结果与实际费用。"""

from __future__ import annotations

import json

import httpx
import pytest

from roundtable.core.config import load_config
from roundtable.core.config.schema import SearchProviderSpec
from roundtable.core.providers import ErrorKind, KeyRing, ProviderError, Secret
from roundtable.core.search import SearchService
from roundtable.core.search.openrouter import OpenRouterSearch

KEY = "sk-or-v1-" + "d" * 48


def spec(**params):
    return SearchProviderSpec(
        adapter="openrouter",
        base_url="https://or.example.test/api/v1",
        key_env="OPENROUTER_API_KEY",
        params={"model": "vendor/cheap", "engine": "exa", **params},
    )


def body(annotations, cost=0.0081):
    return {
        "choices": [
            {"message": {"role": "assistant", "content": "text", "annotations": annotations}}
        ],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20, "cost": cost},
    }


def cite(url, title="T", content="snippet"):
    return {
        "type": "url_citation",
        "url_citation": {
            "url": url,
            "title": title,
            "content": content,
            "start_index": 0,
            "end_index": 4,
        },
    }


def make(handler, **params):
    provider = OpenRouterSearch(
        "openrouter", spec(**params), Secret(KEY), 30, transport=httpx.MockTransport(handler)
    )
    provider.render = lambda q: [{"role": "system", "content": "S"}, {"role": "user", "content": q}]
    return provider


async def test_plugin_request_and_citations():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=body(
                [
                    cite("https://a.test/1", "A", "aaa"),
                    cite("https://a.test/1"),
                    {"type": "other"},
                    cite("ftp://x"),
                    cite("https://b.test/2", "B", "bbb"),
                ]
            ),
        )

    out = await make(handler).search("极值", 5)
    assert seen["url"] == "https://or.example.test/api/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}" and KEY not in json.dumps(seen["body"])
    b = seen["body"]
    assert b["model"] == "vendor/cheap" and b["messages"][1] == {"role": "user", "content": "极值"}
    assert b["plugins"] == [{"id": "web", "engine": "exa", "max_results": 5}]
    assert b["usage"] == {"include": True} and "tools" not in b
    assert [(h.title, h.url, h.snippet) for h in out.hits] == [
        ("A", "https://a.test/1", "aaa"),
        ("B", "https://b.test/2", "bbb"),
    ]
    assert out.cost_usd == pytest.approx(0.0081)  # 以返回的实际费用为准


async def test_server_tool_request():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=body([]))

    out = await make(handler, request="server_tool").search("q", 3)
    assert seen["body"]["tools"] == [
        {"type": "openrouter:web_search", "parameters": {"engine": "exa", "max_results": 3}}
    ]
    assert "plugins" not in seen["body"] and out.hits == ()


async def test_errors_and_no_fetch():
    def handler(request):
        return httpx.Response(402, json={"error": {"code": 402, "message": f"no credits {KEY}"}})

    with pytest.raises(ProviderError) as info:
        await make(handler).search("q", 3)
    assert info.value.kind == ErrorKind.QUOTA and KEY not in str(info.value)
    assert make(handler).supports_fetch is True
    assert make(handler, fetch=False).supports_fetch is False


async def test_default_is_openrouter_and_tavily_optional():
    models = load_config().models
    assert list(models.search_providers)[0] == "openrouter"
    only_or = SearchService.build(models, KeyRing({"OPENROUTER_API_KEY": Secret(KEY)}), 10)
    assert (
        list(only_or.providers) == ["openrouter"] and only_or.can_fetch
    )  # 读取也默认用 OpenRouter
    both = SearchService.build(
        models,
        KeyRing({"OPENROUTER_API_KEY": Secret(KEY), "TAVILY_API_KEY": Secret("tvly-" + "x" * 30)}),
        10,
    )
    assert list(both.providers) == ["openrouter", "tavily"] and both.can_fetch
    await only_or.aclose()
    await both.aclose()


def test_model_required():
    bad = SearchProviderSpec(adapter="openrouter", base_url="https://x.test", params={})
    with pytest.raises(ValueError, match="params.model"):
        OpenRouterSearch("openrouter", bad, None, 10)


async def test_fetch_via_web_fetch_tool():
    seen = []
    page = "这是网页的正文内容，" * 20

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": page}}],
                "usage": {"cost": 0.0012},
            },
        )

    provider = make(handler)
    provider.render_fetch = lambda u: [{"role": "user", "content": f"<url>{u}</url>"}]
    out = await provider.fetch(["https://a.test/1"], 50)
    body = seen[0]
    assert body["tools"] == [{"type": "openrouter:web_fetch"}] and body["model"] == "vendor/cheap"
    assert body["messages"] == [{"role": "user", "content": "<url>https://a.test/1</url>"}]
    assert "plugins" not in body and body["max_tokens"] == 50 // 2 + 200
    assert out.pages[0].ok and out.pages[0].text == page[:50]
    assert out.cost_usd == pytest.approx(0.0012) and provider.relayed_fetch


@pytest.mark.parametrize("content", ["FETCH_FAILED", "", "短"])
async def test_fetch_failure_marker(content):
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    out = await make(handler).fetch(["https://a.test/1"], 1000)
    assert not out.pages[0].ok and out.pages[0].text == "" and out.cost_usd is None


async def test_fetch_prefers_direct_citation_content():
    longer = "原文" * 100

    def handler(request):
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "模型转述的较短内容，只有一句话而已。",
                            "annotations": [cite("https://a.test/1/", "T", longer)],
                        }
                    }
                ]
            },
        )

    out = await make(handler).fetch(["https://a.test/1"], 10000)
    assert out.pages[0].text == longer


async def test_fetch_tool_object_configurable():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "x" * 40}}]})

    tool = {"type": "openrouter:web_fetch", "parameters": {"engine": "openrouter"}}
    await make(handler, fetch_tool=tool).fetch(["https://a.test/1"], 1000)
    assert seen["body"]["tools"] == [tool]
