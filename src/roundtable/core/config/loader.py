"""从 config/ 目录读取 YAML 并校验。失败时抛出带文件名和字段路径的 ConfigError。

本机设置写在同目录的 `<名字>.local.yaml`（如 `roundtable.local.yaml`，不进 git），
按层合并到仓库配置之上：映射逐层合并，其余（列表、标量）整体替换。
这样更新代码（git pull）不会和本机改过的设置冲突。
"""

from __future__ import annotations

import os
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


# 测试设置这个环境变量，跳过本机覆盖文件（tests/conftest.py）
NO_LOCAL_ENV = "ROUNDTABLE_NO_LOCAL_CONFIG"


def local_name(filename: str) -> str:
    """roundtable.yaml → roundtable.local.yaml"""
    stem, _, suffix = filename.rpartition(".")
    return f"{stem}.local.{suffix}"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_dir: Path | str = DEFAULT_CONFIG_DIR) -> AppConfig:
    config_dir = Path(config_dir)
    use_local = not os.environ.get(NO_LOCAL_ENV)
    parts: dict[str, Any] = {}
    for key, (filename, schema) in FILES.items():
        data = _read_yaml(config_dir / filename)
        local = config_dir / local_name(filename)
        if use_local and local.is_file():
            filename = f"{filename} + {local.name}"
            data = deep_merge(data, _read_yaml(local))
        try:
            parts[key] = schema.model_validate(data)
        except ValidationError as exc:
            raise ConfigError(_format_errors(filename, exc)) from None
    try:
        return AppConfig(**parts)
    except ValidationError as exc:
        raise ConfigError(_format_errors("配置交叉校验", exc)) from None
