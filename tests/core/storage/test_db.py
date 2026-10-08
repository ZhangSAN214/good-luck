from __future__ import annotations

import hashlib

import pytest

from roundtable.core.storage import MIGRATIONS, MigrationError, connect, current_version, migrate
from roundtable.core.storage import migrations as migrations_module

# 已发布迁移的哈希。新增迁移时在这里追加一行；已有的行不得修改。
PUBLISHED = {
    1: "933230221ea1f88e4c24d04fc53f637405aa2d31f301751d3a32bad452818567",
    2: "c00e4408420071964033045c732e853efa6f3b3130a3a65131adf9cb2650cf2b",
}


def test_published_migrations_unchanged():
    for number, _, sql in MIGRATIONS:
        if number in PUBLISHED:
            assert hashlib.sha256(sql.encode()).hexdigest() == PUBLISHED[number], (
                f"迁移 {number} 已发布，不能修改；请新增一个迁移"
            )
    assert set(PUBLISHED) == {n for n, _, _ in MIGRATIONS}, "新迁移需要登记哈希"


def test_versions_are_consecutive():
    assert [n for n, _, _ in MIGRATIONS] == list(range(1, len(MIGRATIONS) + 1))


def test_fresh_database_has_all_tables():
    conn = connect()
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "sessions",
        "routing_records",
        "seats",
        "calls",
        "call_attempts",
        "outputs",
        "step_progress",
        "checkpoints",
        "schema_version",
    } <= tables
    assert current_version(conn) == MIGRATIONS[-1][0]
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent_on_file(tmp_path):
    path = tmp_path / "sub" / "rt.db"
    conn = connect(path)
    conn.execute(
        "INSERT INTO sessions (id, created_at, updated_at, question, seed, mode, status)"
        " VALUES ('a', 't', 't', 'q', 1, 'auto', 'created')"
    )
    conn.close()
    conn = connect(path)  # 再次打开：不重复建表、不丢数据
    assert migrate(conn) == MIGRATIONS[-1][0]
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == len(MIGRATIONS)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_newer_database_rejected():
    conn = connect()
    conn.execute("INSERT INTO schema_version VALUES (999, 'future', 'now')")
    with pytest.raises(MigrationError, match="999"):
        migrate(conn)


def test_failed_migration_rolls_back(monkeypatch):
    conn = connect()
    bad = [*MIGRATIONS, (len(MIGRATIONS) + 1, "坏迁移", "CREATE TABLE ok_t (x INT); SELEC broken")]
    monkeypatch.setattr(migrations_module, "MIGRATIONS", bad)
    monkeypatch.setattr("roundtable.core.storage.db.MIGRATIONS", bad)
    with pytest.raises(MigrationError, match="坏迁移"):
        migrate(conn)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_t" not in tables
    assert current_version(conn) == MIGRATIONS[-1][0]
