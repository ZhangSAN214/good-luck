"""公告栏：版本更新说明的校验与自动编号。"""

from __future__ import annotations

import pytest
import yaml

from roundtable.core.changelog import changelog_view, load_changelog
from roundtable.core.config.loader import ConfigError


def rel(pr: int, day: str = "2026-10-27", **kw) -> dict:
    return {"date": day, "pr": [pr], "title": f"改动 {pr}", "added": ["新东西"], **kw}


def write(tmp_path, releases, start=3) -> str:
    path = tmp_path / "changelog.yaml"
    data = {"start": start, "releases": releases}
    path.write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")
    return path


def versions(path) -> list[tuple[str, str]]:
    return [(r["version"], r["id"]) for r in changelog_view(load_changelog(path))["releases"]]


def test_repo_changelog_is_valid_and_newest_first():
    view = changelog_view(load_changelog())
    dates = [r["date"] for r in view["releases"]]
    assert dates == sorted(dates, reverse=True)
    nums = [tuple(int(n) for n in r["version"].split(".")) for r in view["releases"]]
    assert nums == sorted(nums, reverse=True) and len(set(nums)) == len(nums)
    assert all(n[0] == 3 for n in nums)  # 第三代；换代要用户点头
    assert view["latest"] == view["releases"][0]["version"]
    ids = [r["id"] for r in view["releases"]]
    assert len(set(ids)) == len(ids)
    assert all(r["added"] or r["fixed"] for r in view["releases"])


def test_numbers_follow_merge_order_and_level(tmp_path):
    releases = [
        rel(9, "2026-10-29"),
        rel(5, "2026-10-29", level="major"),
        rel(3, "2026-10-28", level="minor"),
        rel(4, "2026-10-28"),  # 同一天按 PR 编号，不看文件先后
        rel(2, "2026-10-28"),
        rel(1, "2026-10-27"),
    ]
    got = versions(write(tmp_path, releases))
    assert got == [
        ("4.0.1", "#9"),
        ("4.0.0", "#5"),
        ("3.1.1", "#4"),
        ("3.1.0", "#3"),
        ("3.0.1", "#2"),
        ("3.0.0", "#1"),
    ]


def test_second_entry_of_the_same_pr_gets_its_own_id(tmp_path):
    # 文件里靠上的是后来补的那条
    got = versions(write(tmp_path, [rel(55, fixed=["补一刀"]), rel(55, level="minor")]))
    assert got == [("3.0.1", "#55.1"), ("3.0.0", "#55")]


@pytest.mark.parametrize(
    ("releases", "msg"),
    [
        ([{**rel(2), "version": "1.2"}], "自动算"),
        ([{**rel(2), "added": []}], "至少写一条"),
        ([{**rel(2), "author": "x"}], "author"),
        ([{**rel(2), "level": "big"}], "level"),
        ([{**rel(2), "pr": []}], "pr"),
        ([], "releases"),
    ],
)
def test_bad_files_name_the_file_and_field(tmp_path, releases, msg):
    with pytest.raises(ConfigError) as err:
        load_changelog(write(tmp_path, releases))
    assert "changelog.yaml" in str(err.value) and msg in str(err.value)
