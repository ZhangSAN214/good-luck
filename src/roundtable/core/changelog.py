"""公告栏：版本更新说明（config/changelog.yaml），终端首页只读展示。

- 每合并一个 PR 就在文件最上面追加一条：日期、PR 编号、级别、标题、新增 / 修复 / 怎么用，
  写给不懂代码的人看。
- 版本号不手写，由代码按合并先后（日期 → PR 编号 → 文件里靠下的在前）自动算，
  几个 PR 同时合并也不会撞号。规则「代.大功能.小改」：
  - 第一条是 ``<start>.0.0``（``start`` 写在文件顶部，圆桌第三代 = 3）；
  - ``level: major``：换代，第一位 +1，后两位归零（只有用户点头才写）；
  - ``level: minor``：新增一整块（新入口、新系统，如工作台、一起看），中间位 +1，末位归零；
  - ``level: patch``（默认）：现有功能的改进和修复，末位 +1。
- 每条另有不变的 ``id``（``#PR 编号``，同一个 PR 第二条起加 ``.1``、``.2``），浏览器按它记已读。
- 每次请求重新读文件，更新说明不用重启服务；已读状态只存在浏览器本机，不进数据库。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

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
    date: date
    pr: list[int] = Field(min_length=1)
    level: Literal["major", "minor", "patch"] = "patch"
    title: str = Field(min_length=1, max_length=40)
    added: list[str] = Field(default_factory=list)
    fixed: list[str] = Field(default_factory=list)
    howto: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_handwritten_version(cls, data: Any) -> Any:
        if isinstance(data, dict) and "version" in data:
            raise ValueError("版本号由程序自动算，删掉 version，改写 level")
        return data

    @model_validator(mode="after")
    def _has_content(self) -> Release:
        if not (self.added or self.fixed):
            raise ValueError("新增（added）和修复（fixed）至少写一条")
        return self


class Changelog(_Strict):
    start: int = Field(ge=1)
    releases: list[Release] = Field(min_length=1)


def load_changelog(path: Path | str = DEFAULT_CONFIG_DIR / CHANGELOG_FILE) -> Changelog:
    path = Path(path)
    data = _read_yaml(path)
    try:
        return Changelog.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_errors(path.name, exc)) from None


def numbered(log: Changelog) -> list[tuple[str, str, Release]]:
    """按合并先后编号：[(版本号, id, 条目)]，最早的在前。"""
    n = len(log.releases)
    # 文件里新的写在上面，同一天同一个 PR 的两条，靠下的先合并
    order = sorted(range(n), key=lambda i: (log.releases[i].date, min(log.releases[i].pr), -i))
    major, minor, patch = log.start, 0, 0
    seen: dict[int, int] = {}
    out = []
    for k, i in enumerate(order):
        r = log.releases[i]
        if k == 0:
            pass
        elif r.level == "major":
            major, minor, patch = major + 1, 0, 0
        elif r.level == "minor":
            minor, patch = minor + 1, 0
        else:
            patch += 1
        first = r.pr[0]
        dup = seen.get(first, 0)
        seen[first] = dup + 1
        rid = f"#{first}" + (f".{dup}" if dup else "")
        out.append((f"{major}.{minor}.{patch}", rid, r))
    return out


def changelog_view(log: Changelog) -> dict[str, Any]:
    """可 JSON 化的公告：新的在前。"""
    releases = [
        {
            "version": v,
            "id": rid,
            "level": r.level,
            "date": r.date.isoformat(),
            "title": r.title,
            "pr": list(r.pr),
            "added": list(r.added),
            "fixed": list(r.fixed),
            "howto": r.howto,
        }
        for v, rid, r in reversed(numbered(log))
    ]
    return {"latest": releases[0]["version"], "releases": releases}
