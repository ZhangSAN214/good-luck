"""联网搜索：服务注册（@register_search）、Tavily 实现、按顺序切换的 SearchService。"""

from . import fake, openrouter, tavily  # noqa: F401 - 注册适配器
from .base import (
    FetchedPage,
    FetchResponse,
    SearchHit,
    SearchProvider,
    SearchResponse,
    register_search,
    search_adapter,
)
from .fake import FakeSearch
from .service import SearchCall, SearchService, SearchUnavailable

__all__ = [
    "FakeSearch",
    "FetchResponse",
    "FetchedPage",
    "SearchCall",
    "SearchHit",
    "SearchProvider",
    "SearchResponse",
    "SearchService",
    "SearchUnavailable",
    "register_search",
    "search_adapter",
]
