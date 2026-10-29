"""公告栏：版本更新说明的校验与排序。"""

from __future__ import annotations

import pytest
import yaml

from roundtable.core.changelog import changelog_view, load_changelog
from roundtable.core.config.loader import ConfigError


def rel(version: str, day: str = "2026-10-27", **kw) -> dict:
    return {"version": version, "date": day, "title": f"版本 {version}", "added": ["新东西"], **kw}


def write(tmp_path, releases) -> str:
    path = tmp_path / "changelog.yaml"
    path.write_text(yaml.safe_dump({"releases": releases}, allow_unicode=True), "utf-8")
    return path


def test_repo_changelog_is_valid_and_newest_first():
    view = changelog_view(load_changelog())
    dates = [r["date"] for r in view["releases"]]
    assert dates == sorted(dates, reverse=True)
    nums = [tuple(int(n) for n in r["version"].split(".")) for r in view["releases"]]
    assert nums == sorted(nums, reverse=True)
    assert view["latest"] == view["releases"][0]["version"]
    assert all(r["added"] or r["fixed"] for r in view["releases"])


def test_orders_by_version_number_not_file_order(tmp_path):
    releases = [rel("1.9"), rel("1.62"), rel("1.10"), rel("1.55"), rel("1.55.1")]
    log = load_changelog(write(tmp_path, releases))
    got = [r["version"] for r in changelog_view(log)["releases"]]
    assert got == ["1.62", "1.55.1", "1.55", "1.10", "1.9"]


@pytest.mark.parametrize(
    ("releases", "msg"),
    [
        ([rel("1.2"), rel("1.2")], "版本号重复"),
        ([{**rel("1.2"), "added": []}], "至少写一条"),
        ([{**rel("1.2"), "author": "x"}], "author"),
        ([rel("v1")], "version"),
        ([], "releases"),
    ],
)
def test_bad_files_name_the_file_and_field(tmp_path, releases, msg):
    with pytest.raises(ConfigError) as err:
        load_changelog(write(tmp_path, releases))
    assert "changelog.yaml" in str(err.value) and msg in str(err.value)
