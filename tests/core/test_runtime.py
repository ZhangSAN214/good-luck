"""运行时组装：从 .env / 环境变量读取 key、构建渠道、打开数据库（不联网）。"""

from __future__ import annotations

import pytest

from roundtable.core.config import ConfigError, load_config
from roundtable.core.runtime import DB_ENV, Runtime


async def test_build_from_environment(tmp_path):
    rt = Runtime.build(
        environ={"OPENROUTER_API_KEY": "placeholder-key-123", DB_ENV: str(tmp_path / "x.db")},
        dotenv_path=None,
    )
    assert list(rt.router._providers) == ["openrouter"]
    assert set(rt.unavailable_channels) == {"openai", "anthropic", "google", "xai", "deepseek"}
    assert len(rt.router.available_models()) == len(rt.config.models.enabled)
    assert (tmp_path / "x.db").exists()
    await rt.aclose()


async def test_build_direct_mode_without_direct_keys_has_no_models(tmp_path):
    cfg = load_config()
    cfg = cfg.model_copy(
        update={"roundtable": cfg.roundtable.model_copy(update={"channel_mode": "direct"})}
    )
    rt = Runtime.build(
        config=cfg,
        environ={"OPENROUTER_API_KEY": "placeholder-key-123"},
        dotenv_path=None,
        db_path=tmp_path / "y.db",
    )
    assert rt.router.available_models() == []
    await rt.aclose()


def test_build_rejects_unregistered_steps(tmp_path):
    cfg = load_config()
    bad = cfg.roundtable.model_copy(update={"pipeline": ["answer", "debate", "synthesize"]})
    with pytest.raises(ConfigError, match="debate"):
        Runtime.build(
            config=cfg.model_copy(update={"roundtable": bad}),
            providers={},
            db_path=tmp_path / "z.db",
        )
