"""插件榜：数据文件校验、排名与门槛判断。"""

from __future__ import annotations

import pytest
import yaml

from roundtable.core.config import load_config
from roundtable.core.config.loader import ConfigError
from roundtable.core.plugin_board import STATUS_LABELS, board_view, load_board

BASE = {
    "updated": "2026-10-26",
    "criteria": {"idea_min_stars": 200, "code_min_stars": 1000, "code_licenses": ["MIT"]},
}


def entry(name: str, **kw) -> dict:
    return {
        "name": name,
        "category": "测试",
        "summary": "是什么",
        "value": "有什么用",
        "status": "candidate",
        "rating": 3,
        **kw,
    }


def write(tmp_path, entries, **extra):
    path = tmp_path / "plugin_board.yaml"
    path.write_text(
        yaml.safe_dump({**BASE, "entries": entries, **extra}, allow_unicode=True), "utf-8"
    )
    return path


def test_shipped_board_is_valid_and_neutral():
    view = board_view(load_board())
    assert view["entries"] and sum(view["counts"].values()) == len(view["entries"])
    assert set(view["counts"]) == set(STATUS_LABELS)
    # 榜单文字展示在网页上：不出现成员昵称（昵称随模型走，避免和成员混淆）
    nicknames = set(load_config().personas.nicknames.values())
    text = yaml.safe_dump(view, allow_unicode=True)
    assert [n for n in nicknames if n in text] == []


def test_rank_by_rating_then_verified_stars_then_name(tmp_path):
    path = write(
        tmp_path,
        [
            entry("b", rating=4),
            entry("a", rating=4),
            entry("c", rating=4, stars=50, checked="2026-10-26"),
            entry("d", rating=4, stars=900, checked="2026-10-26"),
            entry("e", rating=5),
        ],
    )
    assert [e["name"] for e in board_view(load_board(path))["entries"]] == [
        "e",
        "d",
        "c",
        "a",
        "b",
    ]
    assert [e["rank"] for e in board_view(load_board(path))["entries"]] == [1, 2, 3, 4, 5]


def test_thresholds(tmp_path):
    def flags(**kw):
        view = board_view(load_board(write(tmp_path, [entry("x", **kw)])))
        e = view["entries"][0]
        return e["verified"], e["meets_idea"], e["meets_code"]

    assert flags() == (False, False, False)  # 没核实：都不算满足
    assert flags(stars=199, checked="2026-10-26", license="MIT") == (True, False, False)
    assert flags(stars=200, checked="2026-10-26", license="MIT") == (True, True, False)
    assert flags(stars=1000, checked="2026-10-26", license="mit") == (True, True, True)
    assert flags(stars=5000, checked="2026-10-26", license="GPL-3.0") == (True, True, False)
    assert flags(stars=5000, checked="2026-10-26") == (True, True, False)  # 许可证不明


def test_url_from_repo(tmp_path):
    view = board_view(load_board(write(tmp_path, [entry("x", repo="o/r"), entry("y")])))
    urls = {e["name"]: e["url"] for e in view["entries"]}
    assert urls == {"x": "https://github.com/o/r", "y": None}


@pytest.mark.parametrize(
    ("entries", "extra", "where"),
    [
        ([entry("x", oops=1)], {}, "entries.0.oops"),
        ([entry("x", status="maybe")], {}, "entries.0.status"),
        ([entry("x", rating=6)], {}, "entries.0.rating"),
        ([entry("x", repo="不是仓库")], {}, "entries.0.repo"),
        ([entry("x", stars=10)], {}, "checked"),
        ([entry("x"), entry("x")], {}, "名称重复"),
        ([entry("x")], {"extra": 1}, "extra"),
    ],
)
def test_invalid_file_reports_file_and_field(tmp_path, entries, extra, where):
    with pytest.raises(ConfigError) as info:
        load_board(write(tmp_path, entries, **extra))
    assert "plugin_board.yaml" in str(info.value) and where in str(info.value)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_board(tmp_path / "nope.yaml")
