"""配置加载与校验。"""

from .loader import DEFAULT_CONFIG_DIR, ConfigError, load_config
from .schema import (
    AppConfig,
    ChannelMode,
    ChannelSpec,
    ModelsConfig,
    ModelSpec,
    PersonasConfig,
    Price,
    RoundtableConfig,
    Route,
)

__all__ = [
    "DEFAULT_CONFIG_DIR",
    "AppConfig",
    "ChannelMode",
    "ChannelSpec",
    "ConfigError",
    "ModelSpec",
    "ModelsConfig",
    "PersonasConfig",
    "Price",
    "Route",
    "RoundtableConfig",
    "load_config",
]
