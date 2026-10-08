from __future__ import annotations

import pytest

from roundtable.core.config import ConfigError, load_config
from roundtable.core.providers import (
    FakeProvider,
    KeyRing,
    adapter_names,
    build_providers,
    register_adapter,
)
from roundtable.core.providers.anthropic_adapter import AnthropicProvider
from roundtable.core.providers.gemini import GeminiProvider
from roundtable.core.providers.openai_compat import OpenAICompatProvider

from .conftest import FAKE_KEYS, models_config


def test_builtin_adapters_registered():
    assert {"openai_compat", "anthropic", "gemini", "fake"} <= set(adapter_names())


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError):
        register_adapter("fake")(lambda *a: None)


def test_unknown_adapter_is_config_error():
    cfg = models_config({"x": "direct"}, {"m": ["x"]})
    data = cfg.model_dump()
    data["channels"]["x"]["adapter"] = "telepathy"
    cfg = type(cfg).model_validate(data)
    with pytest.raises(ConfigError, match="telepathy"):
        build_providers(cfg, KeyRing({}), 30)


def test_repo_config_with_all_keys_builds_expected_adapters():
    cfg = load_config()
    keys = KeyRing.from_env(list(FAKE_KEYS), environ=FAKE_KEYS)
    providers, unavailable = build_providers(cfg.models, keys, 30)
    assert unavailable == {}
    expected = {
        "openrouter": OpenAICompatProvider,
        "openai": OpenAICompatProvider,
        "xai": OpenAICompatProvider,
        "deepseek": OpenAICompatProvider,
        "anthropic": AnthropicProvider,
        "google": GeminiProvider,
    }
    assert {name: type(p) for name, p in providers.items()} == expected


def test_repo_config_with_only_openrouter_key():
    cfg = load_config()
    keys = KeyRing.from_env(list(FAKE_KEYS), environ={"OPENROUTER_API_KEY": "placeholder-123"})
    providers, unavailable = build_providers(cfg.models, keys, 30)
    assert list(providers) == ["openrouter"]
    assert set(unavailable) == {"openai", "anthropic", "google", "xai", "deepseek"}


def test_keyless_channel_built_without_key():
    cfg = models_config({"local": "local"}, {"m": ["local"]})
    data = cfg.model_dump()
    data["channels"]["local"]["key_env"] = None
    cfg = type(cfg).model_validate(data)
    providers, unavailable = build_providers(cfg, KeyRing({}), 30)
    assert isinstance(providers["local"], FakeProvider) and unavailable == {}
