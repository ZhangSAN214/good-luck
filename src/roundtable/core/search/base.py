"""联网搜索服务：可插拔（@register_search），与渠道适配器同样的注册方式。

一家服务一个实例；key 包成 Secret，只在组装请求头时 reveal()；外部错误文本脱敏、截断后放进
ProviderError（与模型渠道相同的错误分类）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from roundtable.core.config.schema import SearchProviderSpec
from roundtable.core.providers.secrets import Secret


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class FetchedPage:
    url: str
    text: str
    ok: bool = True


@dataclass(frozen=True)
class SearchResponse:
    hits: tuple[SearchHit, ...]
    cost_usd: float | None = None  # 服务返回的实际费用（没有时按配置单价计）


@dataclass(frozen=True)
class FetchResponse:
    pages: tuple[FetchedPage, ...]
    cost_usd: float | None = None


class SearchProvider(ABC):
    supports_fetch = True  # 能否读取网页正文（fetch 工具）

    def __init__(self, name: str, spec: SearchProviderSpec) -> None:
        self.name = name
        self.spec = spec

    @abstractmethod
    async def search(self, query: str, max_results: int) -> SearchResponse:
        """失败时只抛 ProviderError。"""

    @abstractmethod
    async def fetch(self, urls: list[str], max_chars: int) -> FetchResponse:
        """读取网页正文。失败时只抛 ProviderError。"""

    async def aclose(self) -> None:  # noqa: B027 - 可选覆盖
        """释放连接。"""


Factory = Callable[[str, SearchProviderSpec, Secret | None, float], SearchProvider]
_REGISTRY: dict[str, Factory] = {}


def register_search(name: str) -> Callable[[Factory], Factory]:
    def deco(factory: Factory) -> Factory:
        if name in _REGISTRY:
            raise ValueError(f"搜索适配器 {name!r} 重复注册")
        _REGISTRY[name] = factory
        return factory

    return deco


def search_adapter(name: str) -> Factory:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ValueError(f"未知的搜索适配器 {name!r}，可选：{sorted(_REGISTRY)}") from None
