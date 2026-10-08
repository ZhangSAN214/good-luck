"""适配器注册表与渠道构建。新增适配器：写一个类并用 @register_adapter 注册。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from roundtable.core.config import ConfigError, ModelsConfig
from roundtable.core.config.schema import ChannelSpec

from .base import Provider
from .secrets import KeyRing, Secret


class AdapterFactory(Protocol):
    def __call__(
        self, channel: str, spec: ChannelSpec, key: Secret | None, timeout_s: float
    ) -> Provider: ...


_ADAPTERS: dict[str, AdapterFactory] = {}

REASON_NO_KEY = "缺少 key"


def register_adapter(name: str) -> Callable[[AdapterFactory], AdapterFactory]:
    def decorator(factory: AdapterFactory) -> AdapterFactory:
        if name in _ADAPTERS:
            raise ValueError(f"适配器 {name!r} 已注册")
        _ADAPTERS[name] = factory
        return factory

    return decorator


def adapter_names() -> list[str]:
    return sorted(_ADAPTERS)


def build_providers(
    models: ModelsConfig, keys: KeyRing, timeout_s: float
) -> tuple[dict[str, Provider], dict[str, str]]:
    """为每个渠道构建 Provider。

    返回 (可用渠道 → Provider, 不可用渠道 → 原因)。需要 key 但没有 key 的渠道被跳过。
    """
    unknown = {
        name: spec.adapter
        for name, spec in models.channels.items()
        if spec.adapter not in _ADAPTERS
    }
    if unknown:
        raise ConfigError(f"未知的适配器：{unknown}；可用：{adapter_names()}")

    providers: dict[str, Provider] = {}
    unavailable: dict[str, str] = {}
    for name, spec in models.channels.items():
        key = keys.get(spec.key_env)
        if spec.key_env and key is None:
            unavailable[name] = REASON_NO_KEY
            continue
        providers[name] = _ADAPTERS[spec.adapter](name, spec, key, timeout_s)
    return providers, unavailable
