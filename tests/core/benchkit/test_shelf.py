"""项目列表不堆积（shelf.py）：自动收起、回收站、过期清理。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.benchkit import shelf

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


def test_folded_rules():
    assert not shelf.folded({"status": "delivered", "created_at": iso(100)}, NOW)
    assert not shelf.folded({"status": "failed", "created_at": iso(1)}, NOW)
    assert shelf.folded({"status": "failed", "created_at": iso(4)}, NOW)
    assert shelf.folded({"status": "cancelled", "finished_at": iso(5), "created_at": iso(9)}, NOW)
    assert shelf.folded({"status": "delivered", "shelved": True}, NOW)  # 你点了收起
    assert not shelf.folded({"status": "failed", "created_at": iso(9), "shelved": False}, NOW)
    assert not shelf.folded({"status": "failed", "created_at": "乱写"}, NOW)


def make(root, bid, title="台"):
    (root / bid).mkdir(parents=True)
    (root / bid / "build.json").write_text(json.dumps({"id": bid, "title": title}), "utf-8")


def test_trash_restore_and_purge(tmp_path):
    make(tmp_path, "a", "旅行台")
    shelf.to_trash(tmp_path, "a", NOW - timedelta(days=31))
    make(tmp_path, "b")
    shelf.to_trash(tmp_path, "b", NOW)
    assert not (tmp_path / "a").exists()
    items = shelf.trash_list(tmp_path, NOW)
    assert [(x["id"], x["left"]) for x in items] == [("b", 30), ("a", 0)]
    assert items[1]["title"] == "旅行台"
    with pytest.raises(KeyError):
        shelf.to_trash(tmp_path, "nope", NOW)
    assert shelf.purge(tmp_path, NOW) == 1  # 只清放满 30 天的
    assert [x["id"] for x in shelf.trash_list(tmp_path, NOW)] == ["b"]
    make(tmp_path, "b")
    with pytest.raises(RuntimeError):  # 原处已有同名项目：不覆盖
        shelf.restore(tmp_path, "b")
    (tmp_path / "b" / "build.json").unlink()
    (tmp_path / "b").rmdir()
    shelf.restore(tmp_path, "b")
    assert (tmp_path / "b" / "build.json").is_file()
    with pytest.raises(KeyError):
        shelf.restore(tmp_path, "b")
    assert shelf.trash_list(None, NOW) == [] and shelf.purge(None, NOW) == 0
