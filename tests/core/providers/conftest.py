from __future__ import annotations

from typing import Any

import pytest

from roundtable.core.config import ModelsConfig
from roundtable.core.config.schema import ChannelSpec

# 测试用的假 key：格式像真 key，用来验证脱敏；不是真实凭据
FAKE_KEYS = {
    "OPENROUTER_API_KEY": "sk-or-v1-" + "f" * 48,
    "OPENAI_API_KEY": "sk-proj-" + "A1b2C3d4" * 6,
    "ANTHROPIC_API_KEY": "sk-ant-api03-" + "Z" * 40,
    "GEMINI_API_KEY": "AIza" + "x" * 35,
    "XAI_API_KEY": "xai-" + "q" * 40,
    "DEEPSEEK_API_KEY": "sk-" + "d" * 32,
}


def channel(adapter: str = "openai_compat", kind: str = "direct", **kw: Any) -> ChannelSpec:
    return ChannelSpec(
        adapter=adapter,
        kind=kind,
        base_url=kw.pop("base_url", "https://api.example.test/v1"),
        key_env=kw.pop("key_env", "TEST_API_KEY"),
        **kw,
    )


def models_config(
    channels: dict[str, str], models: dict[str, list[str]], price=(1.0, 2.0)
) -> ModelsConfig:
    """channels: 渠道名 → kind；models: 模型 id → 渠道顺序。"""
    return ModelsConfig.model_validate(
        {
            "tag_vocabulary": ["math"],
            "channels": {
                name: {
                    "adapter": "fake",
                    "kind": kind,
                    "base_url": f"https://{name}.test",
                    "key_env": f"{name.upper()}_API_KEY",
                }
                for name, kind in channels.items()
            },
            "models": [
                {
                    "id": mid,
                    "vendor": "V",
                    "price": {"input": price[0], "output": price[1]},
                    "routes": [{"channel": c, "model": f"{c}/{mid}"} for c in order],
                }
                for mid, order in models.items()
            ],
        }
    )


@pytest.fixture
def fake_keys() -> dict[str, str]:
    return dict(FAKE_KEYS)
