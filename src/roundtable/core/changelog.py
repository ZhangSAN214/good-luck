"""公告栏：版本更新说明（config/changelog.yaml），终端首页只读展示。

- 每合并一个 PR 就在文件最上面追加一条：版本号、日期、标题、新增 / 修复 / 怎么用，
  写给不懂代码的人看。
- 版本号不能重复；顺序由代码按日期（新的在前）排，同一天按文件里的先后。
- 每次请求重新读文件，更新说明不用重启服务；已读状态只存在浏览器本机，不进数据库。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from roundtable.core.config.loader import (
    DEFAULT_CONFIG_DIR,
    ConfigError,
    _format_errors,
    _read_yaml,
)

CHANGELOG_FILE = "changelog.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Release(_Strict):
    version: str = Field(pattern=r"^\d+\.\d+(\.\d+)?$")
    date: date
    title: str = Field(min_length=1, max_length=40)
    pr: list[int] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    fixed: list[str] = Field(default_factory=list)
    howto: str | None = None

    @model_validator(mode="after")
    def _has_content(self) -> Release:
        if not (self.added or self.fixed):
            raise ValueError("新增（added）和修复（fixed）至少写一条")
        return self


class Changelog(_Strict):
    releases: list[Release] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_versions(self) -> Changelog:
        names = [r.version for r in self.releases]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"版本号重复：{dup}")
        return self


def load_changelog(path: Path | str = DEFAULT_CONFIG_DIR / CHANGELOG_FILE) -> Changelog:
    path = Path(path)
    data = _read_yaml(path)
    try:
        return Changelog.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_errors(path.name, exc)) from None


def changelog_view(log: Changelog) -> dict[str, Any]:
    """可 JSON 化的公告：新的在前（同一天保持文件里的先后）。"""
    order = sorted(enumerate(log.releases), key=lambda p: (-p[1].date.toordinal(), p[0]))
    releases = [
        {
            "version": r.version,
            "date": r.date.isoformat(),
            "title": r.title,
            "pr": list(r.pr),
            "added": list(r.added),
            "fixed": list(r.fixed),
            "howto": r.howto,
        }
        for _, r in order
    ]
    return {"latest": releases[0]["version"], "releases": releases}
