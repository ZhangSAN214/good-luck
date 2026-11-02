"""公告栏：版本更新说明，终端首页只读展示。

- 每个 PR 在 ``config/changelog.d/<PR 编号>.yaml`` 新建一个小文件（日期、PR 编号、级别、标题、
  新增 / 修复 / 怎么用），写给不懂代码的人看。几个 PR 同时开时各写各的文件，不会互相冲突。
  ``config/changelog.yaml`` 只放 ``start`` 和拆分前的旧条目（保持兼容，不再往里加）。
- 读的时候把所有小文件和旧条目合在一起，版本号不手写，由代码按合并先后
  （日期 → PR 编号 → 同一 PR 的文件里靠下的在前）自动算，
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
# 每个 PR 一个小文件，放在 changelog.yaml 旁边的这个目录里
CHANGELOG_DIR = "changelog.d"


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


class _Header(_Strict):
    """changelog.yaml 本身：``start`` + 拆分前的旧条目（可以没有）。"""

    start: int = Field(ge=1)
    releases: list[Release] = Field(default_factory=list)


class _Fragment(_Strict):
    """changelog.d 里的一个小文件写成 ``releases:`` 列表时（同一个 PR 写好几条）。"""

    releases: list[Release] = Field(min_length=1)


def _fragment_key(path: Path) -> tuple[int, int | str]:
    # 数字文件名按数值排（99 在 100 前面），其他名字排在后面
    return (0, int(path.stem)) if path.stem.isdigit() else (1, path.stem)


def _load_fragment(path: Path) -> list[Release]:
    name = f"{CHANGELOG_DIR}/{path.name}"
    try:
        data = _read_yaml(path)
    except ConfigError as exc:
        raise ConfigError(str(exc).replace(path.name, name, 1)) from None
    try:
        if "releases" in data:
            releases = list(_Fragment.model_validate(data).releases)
        else:  # 只写一条时直接写字段，不用套 releases
            releases = [Release.model_validate(data)]
    except ValidationError as exc:
        raise ConfigError(_format_errors(name, exc)) from None
    if path.stem.isdigit():
        pr = int(path.stem)
        for k, r in enumerate(releases):
            if pr not in r.pr:
                raise ConfigError(
                    f"{name} 校验失败：\n  - releases.{k}.pr: 文件名是 PR 编号 {pr}，"
                    f"条目的 pr 里要有 {pr}（现在是 {list(r.pr)}）"
                )
    return releases


def load_changelog(path: Path | str = DEFAULT_CONFIG_DIR / CHANGELOG_FILE) -> Changelog:
    """读 changelog.yaml 和旁边 changelog.d/ 里的所有小文件，合成一份公告。"""
    path = Path(path)
    data = _read_yaml(path)
    try:
        header = _Header.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_errors(path.name, exc)) from None
    fragments: list[Release] = []
    folder = path.parent / CHANGELOG_DIR
    if folder.is_dir():
        files = sorted(
            (p for p in folder.iterdir() if p.suffix in (".yaml", ".yml") and p.is_file()),
            key=_fragment_key,
        )
        for f in files:
            fragments.extend(_load_fragment(f))
    # 小文件当作写在旧条目「上面」（更新）：同一天同一个 PR 时，编号排在旧条目后面
    releases = fragments + list(header.releases)
    if not releases:
        raise ConfigError(
            f"{path.name} 校验失败：\n  - releases: 一条公告都没有"
            f"（在 {CHANGELOG_DIR}/ 里加一个 <PR 编号>.yaml）"
        )
    return Changelog(start=header.start, releases=releases)


def numbered(log: Changelog) -> list[tuple[str, str, Release]]:
    """按合并先后编号：[(版本号, id, 条目)]，最早的在前。"""
    n = len(log.releases)
    # 新的写在上面（小文件整体算在旧条目上面），同一天同一个 PR 的两条，靠下的先合并
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
