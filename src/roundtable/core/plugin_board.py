"""插件榜：自我学习革新调研过的高质量插件 / 项目（config/plugin_board.yaml，docs/EVOLUTION.md）。

- 数据由每周小改更新，网页只读展示；不安装、不运行任何插件。
- 排名由代码算：对圆桌的价值（rating）→ 核实过的 star → 名称。
- 门槛看 ``criteria``：借思路要 ``idea_min_stars``；搬代码还要 ``code_min_stars`` 且许可证在
  ``code_licenses`` 里。star 没核实时两项都算「不满足」，界面显示「待核实」。
- 每次请求重新读文件，更新数据不用重启服务。
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

BOARD_FILE = "plugin_board.yaml"
Status = Literal["borrowed", "candidate", "watch", "rejected"]
STATUS_LABELS: dict[str, str] = {
    "borrowed": "已借鉴",
    "candidate": "待借",
    "watch": "观察",
    "rejected": "不借",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Criteria(_Strict):
    idea_min_stars: int = Field(ge=0)
    code_min_stars: int = Field(ge=0)
    code_licenses: list[str] = Field(min_length=1)


class Entry(_Strict):
    name: str = Field(min_length=1)
    repo: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    category: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    value: str = Field(min_length=1)
    status: Status
    rating: int = Field(ge=1, le=5)
    stars: int | None = Field(default=None, ge=0)
    license: str | None = None
    checked: date | None = None
    borrowed_in: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _stars_need_date(self) -> Entry:
        if self.stars is not None and self.checked is None:
            raise ValueError("填了 stars 就要写 checked（核实日期）")
        return self


class PluginBoard(_Strict):
    updated: date
    criteria: Criteria
    entries: list[Entry] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_names(self) -> PluginBoard:
        names = [e.name for e in self.entries]
        dup = sorted({n for n in names if names.count(n) > 1})
        if dup:
            raise ValueError(f"名称重复：{dup}")
        return self


def load_board(path: Path | str = DEFAULT_CONFIG_DIR / BOARD_FILE) -> PluginBoard:
    path = Path(path)
    data = _read_yaml(path)
    try:
        return PluginBoard.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_errors(path.name, exc)) from None


def ranked(board: PluginBoard) -> list[Entry]:
    return sorted(
        board.entries,
        key=lambda e: (-e.rating, e.stars is None, -(e.stars or 0), e.name),
    )


def board_view(board: PluginBoard) -> dict[str, Any]:
    """可 JSON 化的榜单：排名、门槛判断、各状态数量。"""
    c = board.criteria
    licenses = {lic.lower() for lic in c.code_licenses}
    items = []
    for rank, e in enumerate(ranked(board), 1):
        idea_ok = e.stars is not None and e.stars >= c.idea_min_stars
        items.append(
            {
                "rank": rank,
                "name": e.name,
                "repo": e.repo,
                "url": f"https://github.com/{e.repo}" if e.repo else None,
                "category": e.category,
                "summary": e.summary,
                "value": e.value,
                "status": e.status,
                "status_label": STATUS_LABELS[e.status],
                "rating": e.rating,
                "stars": e.stars,
                "license": e.license,
                "checked": e.checked.isoformat() if e.checked else None,
                "borrowed_in": e.borrowed_in,
                "note": e.note,
                "verified": e.stars is not None,
                "meets_idea": idea_ok,
                "meets_code": idea_ok
                and e.stars >= c.code_min_stars  # type: ignore[operator]
                and (e.license or "").lower() in licenses,
            }
        )
    counts = {s: sum(1 for e in board.entries if e.status == s) for s in STATUS_LABELS}
    return {
        "updated": board.updated.isoformat(),
        "criteria": c.model_dump(),
        "status_labels": STATUS_LABELS,
        "counts": counts,
        "categories": sorted({e.category for e in board.entries}),
        "entries": items,
    }
