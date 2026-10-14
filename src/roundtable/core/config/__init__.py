"""配置加载与校验。"""

from .loader import DEFAULT_CONFIG_DIR, ConfigError, load_config
from .schema import (
    DIFFICULTIES,
    AppConfig,
    ChannelMode,
    ChannelSpec,
    MediaPrice,
    MediaRules,
    ModelsConfig,
    ModelSpec,
    PersonasConfig,
    Price,
    RoundtableConfig,
    Route,
)

__all__ = [
    "DEFAULT_CONFIG_DIR",
    "DIFFICULTIES",
    "AppConfig",
    "ChannelMode",
    "ChannelSpec",
    "ConfigError",
    "MediaPrice",
    "MediaRules",
    "ModelSpec",
    "ModelsConfig",
    "PersonasConfig",
    "Price",
    "Route",
    "RoundtableConfig",
    "RoutingConfig",
    "load_config",
]
