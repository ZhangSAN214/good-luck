"""数据库要升级时先存「迁移前」存档点；存不成就不升级（宁可不启动也不冒险改数据）。"""

from __future__ import annotations

import sqlite3

import pytest

from roundtable.core.runtime import Runtime
from roundtable.core.storage import connect
from roundtable.core.storage import db as dbmod
from roundtable.core.storage.db import MigrationError, current_version, pending
from roundtable.core.storage.migrations import MIGRATIONS


def _old_database(db, monkeypatch):
    """上一版程序留下的数据库：少最后一条迁移。"""
    with monkeypatch.context() as m:
        m.setattr(dbmod, "MIGRATIONS", MIGRATIONS[:-1])
        connect(db).close()


def test_new_database_needs_no_checkpoint(tmp_path):
    calls = []
    connect(tmp_path / "a.db", before_migrate=calls.append).close()
    assert calls == []  # 新库：没有数据可保护


def test_checkpoint_before_pending_migrations(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    _old_database(db, monkeypatch)
    calls = []
    conn = connect(db, before_migrate=lambda c: calls.append(pending(c)))
    assert calls == [[MIGRATIONS[-1][0]]]
    assert current_version(conn) == MIGRATIONS[-1][0]
    conn.close()


def test_failed_checkpoint_blocks_migration(tmp_path, monkeypatch):
    db = tmp_path / "a.db"
    _old_database(db, monkeypatch)

    def fail(_):
        raise MigrationError("磁盘满了")

    with pytest.raises(MigrationError):
        connect(db, before_migrate=fail)
    raw = sqlite3.connect(db)
    assert raw.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] < MIGRATIONS[-1][0]
    raw.close()


async def test_runtime_saves_checkpoint_before_upgrade(tmp_path, monkeypatch):
    db = tmp_path / "rt.db"
    _old_database(db, monkeypatch)
    rt = Runtime.build(providers={}, db_path=db, dotenv_path=None)
    names = [p.name for p in (tmp_path / "db_backups").iterdir()]
    assert len(names) == 1 and names[0].endswith("-迁移前.db")
    await rt.aclose()
    # 已经是最新：不再存
    rt = Runtime.build(providers={}, db_path=db, dotenv_path=None)
    assert len(list((tmp_path / "db_backups").iterdir())) == 1
    await rt.aclose()


async def test_runtime_refuses_to_upgrade_without_checkpoint(tmp_path, monkeypatch):
    db = tmp_path / "rt.db"
    _old_database(db, monkeypatch)
    (tmp_path / "db_backups").write_text("这里被一个文件占住了", encoding="utf-8")
    with pytest.raises(MigrationError, match="迁移前"):
        Runtime.build(providers={}, db_path=db, dotenv_path=None)
