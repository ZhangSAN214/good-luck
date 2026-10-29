"""dsh 运行时版本与插件白名单（``config/dsh_plugins.yaml``）：只读，校验失败带文件名和字段路径。"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

CATALOG_FILE = "dsh_plugins.yaml"

_ID = re.compile(r"^[a-z][a-z0-9-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DshRuntime(_Strict):
    package: str = Field(min_length=1)
    version: str = Field(min_length=1)  # 钉死的版本号（不接受范围）
    node_min: int = Field(default=22, ge=18)

    @field_validator("version")
    @classmethod
    def _exact(cls, v: str) -> str:
        if not re.match(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$", v):
            raise ValueError("version 必须是确切的版本号（如 0.2.1-rc.3），不能写范围")
        return v


class DshPlugin(_Strict):
    id: str
    name: str = Field(min_length=1)
    kind: Literal["panel", "tool", "skill", "subagent", "other"]
    url: str
    sha256: str
    version: str = Field(min_length=1)
    dsh: list[str] = Field(min_length=1)  # 插件声明兼容的 dsh 版本
    license: str = Field(min_length=1)
    repo: str | None = None
    stars: int | None = Field(default=None, ge=0)
    checked: date | None = None
    reviewed: str | None = None  # 审核者看过的源码提交号
    note: str = ""
    enabled: bool = True

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("id 只能用小写字母、数字和连字符，并以字母开头")
        return v

    @field_validator("url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("安装包地址只接受 https://")
        return v

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        if not _SHA256.match(v):
            raise ValueError("sha256 必须是 64 位小写十六进制")
        return v


class DshCatalog(_Strict):
    runtime: DshRuntime
    plugins: list[DshPlugin] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> DshCatalog:
        ids = [p.id for p in self.plugins]
        if len(ids) != len(set(ids)):
            raise ValueError("plugins 里有重复的 id")
        for p in self.plugins:
            if p.enabled and self.runtime.version not in p.dsh:
                raise ValueError(
                    f"插件 {p.id} 声明兼容的 dsh 版本 {p.dsh} 不含运行时版本 {self.runtime.version}"
                )
        return self

    @property
    def enabled(self) -> list[DshPlugin]:
        return [p for p in self.plugins if p.enabled]

    def summary(self) -> list[dict[str, object]]:
        """给界面看的白名单（只读）。"""
        return [
            {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "version": p.version,
                "license": p.license,
                "repo": p.repo,
                "stars": p.stars,
                "note": p.note,
                "enabled": p.enabled,
            }
            for p in self.plugins
        ]


class CatalogError(ValueError):
    pass


def load_catalog(path: Path) -> DshCatalog:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"{path.name}：读不到文件（{exc.strerror}）") from None
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path.name}：YAML 格式错误（{exc}）") from None
    try:
        return DshCatalog.model_validate(data or {})
    except ValidationError as exc:
        lines = [
            f"{path.name}: {'.'.join(str(x) for x in e['loc']) or '(根)'}: {e['msg']}"
            for e in exc.errors()
        ]
        raise CatalogError("\n".join(lines)) from None
