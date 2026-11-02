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


# --- 每个 PR 一个小文件（config/changelog.d/<PR>.yaml） ------------------------------


def frag(tmp_path, name: str, data) -> None:
    folder = tmp_path / "changelog.d"
    folder.mkdir(exist_ok=True)
    (folder / name).write_text(yaml.safe_dump(data, allow_unicode=True), "utf-8")


def test_fragments_merge_with_the_old_list_and_number_by_date_then_pr(tmp_path):
    path = write(tmp_path, [rel(2, "2026-10-28"), rel(1, "2026-10-27")])
    frag(tmp_path, "10.yaml", rel(10, "2026-10-29"))  # 只写一条：直接写字段
    frag(tmp_path, "4.yaml", rel(4, "2026-10-28", level="minor"))
    frag(tmp_path, "100.yaml", {"releases": [rel(100, "2026-10-29")]})
    assert versions(path) == [
        ("3.1.2", "#100"),
        ("3.1.1", "#10"),
        ("3.1.0", "#4"),
        ("3.0.1", "#2"),
        ("3.0.0", "#1"),
    ]


def test_header_may_have_no_old_entries(tmp_path):
    path = tmp_path / "changelog.yaml"
    path.write_text("start: 3\n", "utf-8")
    frag(tmp_path, "7.yaml", rel(7))
    assert versions(path) == [("3.0.0", "#7")]


def test_same_pr_in_old_list_and_fragment_fragment_counts_as_later(tmp_path):
    path = write(tmp_path, [rel(55, level="minor")])
    frag(tmp_path, "55.yaml", rel(55, fixed=["补一刀"]))
    assert versions(path) == [("3.0.1", "#55.1"), ("3.0.0", "#55")]


def test_several_entries_in_one_fragment_keep_their_order(tmp_path):
    path = tmp_path / "changelog.yaml"
    path.write_text("start: 3\n", "utf-8")
    frag(tmp_path, "8.yaml", {"releases": [rel(8, fixed=["后补"]), rel(8, level="minor")]})
    assert versions(path) == [("3.0.1", "#8.1"), ("3.0.0", "#8")]


def test_non_yaml_files_in_the_folder_are_ignored(tmp_path):
    path = write(tmp_path, [rel(1)])
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "README.md").write_text("说明", "utf-8")
    assert versions(path) == [("3.0.0", "#1")]


@pytest.mark.parametrize(
    ("name", "data", "msg"),
    [
        ("12.yaml", rel(13), "PR 编号 12"),
        ("12.yaml", {**rel(12), "author": "x"}, "author"),
        ("12.yaml", {"releases": []}, "releases"),
        ("12.yaml", {**rel(12), "version": "3.1.0"}, "自动算"),
    ],
)
def test_bad_fragment_names_the_fragment_file(tmp_path, name, data, msg):
    path = write(tmp_path, [rel(1)])
    frag(tmp_path, name, data)
    with pytest.raises(ConfigError) as err:
        load_changelog(path)
    assert f"changelog.d/{name}" in str(err.value) and msg in str(err.value)


def test_broken_yaml_fragment_names_the_fragment_file(tmp_path):
    path = write(tmp_path, [rel(1)])
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "9.yaml").write_text("date: [\n", "utf-8")
    with pytest.raises(ConfigError) as err:
        load_changelog(path)
    assert "changelog.d/9.yaml" in str(err.value)


def test_no_entries_anywhere_is_an_error(tmp_path):
    path = tmp_path / "changelog.yaml"
    path.write_text("start: 3\n", "utf-8")
    with pytest.raises(ConfigError) as err:
        load_changelog(path)
    assert "changelog.d" in str(err.value)


def test_repo_fragments_are_named_after_their_pr():
    from roundtable.core.changelog import CHANGELOG_DIR, CHANGELOG_FILE
    from roundtable.core.config.loader import DEFAULT_CONFIG_DIR

    folder = DEFAULT_CONFIG_DIR / CHANGELOG_DIR
    assert (DEFAULT_CONFIG_DIR / CHANGELOG_FILE).is_file()
    for f in folder.glob("*"):
        if f.name == "README.md":
            continue
        # 一律 <PR 编号>.yaml：一眼看出是哪个 PR 的，几个 PR 也不会撞名
        assert f.suffix == ".yaml" and f.stem.isdigit(), f.name
