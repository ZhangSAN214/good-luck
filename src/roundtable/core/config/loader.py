"""从 config/ 目录读取 YAML 并校验。失败时抛出带文件名和字段路径的 ConfigError。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .schema import AppConfig, ModelsConfig, PersonasConfig, RoundtableConfig, RoutingConfig

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[4] / "config"

FILES = {
    "models": ("models.yaml", ModelsConfig),
    "roundtable": ("roundtable.yaml", RoundtableConfig),
    "personas": ("personas.yaml", PersonasConfig),
    "routing": ("routing.yaml", RoutingConfig),
}


class ConfigError(ValueError):
    """配置文件缺失、格式错误或校验失败。"""


def _format_errors(source: str, exc: ValidationError) -> str:
    lines = [f"{source} 校验失败："]
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(根)"
        lines.append(f"  - {loc}: {err['msg']}")
    return "\n".join(lines)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"找不到配置文件：{path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} 不是合法的 YAML：{exc}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name} 的顶层必须是映射（key: value）")
    return data


def load_config(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> AppConfig:
    config_dir = Path(config_dir)
    parts: dict[str, Any] = {}
    for key, (filename, schema) in FILES.items():
        data = _read_yaml(config_dir / filename)
        try:
            parts[key] = schema.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(_format_errors(filename, exc)) from None
    try:
        return AppConfig(**parts)
    except ValidationError as exc:
        raise ConfigError(_format_errors("配置交叉校验", exc)) from None
