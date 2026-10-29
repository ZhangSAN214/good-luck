from __future__ import annotations

import hashlib

import pytest

from roundtable.core.storage import MIGRATIONS, MigrationError, connect, current_version, migrate
from roundtable.core.storage import migrations as migrations_module

# 已发布迁移的哈希。新增迁移时在这里追加一行；已有的行不得修改。
PUBLISHED = {
    1: "933230221ea1f88e4c24d04fc53f637405aa2d31f301751d3a32bad452818567",
    2: "c00e4408420071964033045c732e853efa6f3b3130a3a65131adf9cb2650cf2b",
    3: "9e510c35c3870f79db2aaad4478e8b674be7702f36983aba04e0b93a4e55e6fa",
    4: "4c202ff02e1d76abf0ad262d1f56faad375f60487f4c4ba0c90e713ad5ce1c7d",
    5: "91e01e5fc14d47c5959ff3247c3425a85f89354796191616e775f8a32aef18ce",
    6: "f77327855f888076ad9818ccc1e2ce160cfaa43ecbc08964b911d15d26c82822",
    7: "bb9a66c980a8d8b46caa621a7630f2145ce3349d054c77f8d2987aff81abfaea",
    8: "69c7da093c7d966403f83e3e4c8a1079ff4b0f5f77b044a05f0ac2a551040ddc",
    9: "c592f107dc709607dc5205386736845b8fb9d5683f1054e9cdc0904662f78dc1",
    10: "26ee2d7cdc87fa6821f21f00d3892cdc9ae304ad2f860a6543f1a45bc556ceb5",
    11: "7225c4e0cf882f2a267135644220b259ed24ec6cebf47e01b1539155fd0dd384",
    12: "e2846f6ef95e087ba049e3c8370d117f4af8faf98743aeb44ceec74068309fa9",
    13: "ab6106c0b436f78e28fb29f474cd4c54d41fc112866578f4b498dbb9d9587e53",
    14: "acaf5b6d5100af8d7a7795cd6767392e931eaf86fcb8c807414f40b238c3d547",
    15: "ea62556fc35241f17d722cd519e786b79e022ab29b21e5b1adda7be8a40e9e9a",
    16: "6ea1a766085171ea689bfd623d7f8c62463b04467512f7b89ecbe898e86ba302",
    17: "d269dcf37e7a8f6065ffb77009eb0a4dfeec6bfb7410b958819cafc7889ac51a",
    18: "914adbf0d7e5b7ecc3c6cbc8e530f67841615b9e6f126985cf61d1de5dac83f2",
    19: "3e65d880bbd40dd7e1a0883c7be74aaeb2d208b15fcccb80ea9e8bca748d38d7",
    20: "dc8b989a2a0bcdae0171d3c1f28027d289aaa07a6af738bf962f0001c478b98e",
    21: "e4c2debbe4ca9c5ec627ca799417d2c97a5384f91b761ea0f4f461637a0c5072",
    22: "a042b995e7f8839bcce5d8532e41c4f9f0f0236076b85624604bb5cbdc718764",
    23: "293447897496e063aadbacd66d0f4f4ed5be3b620d0486c1380baa5e66444377",
    24: "0633d3a46cba4dcb3ba531a3551e071fcfe7f365cf772c3dfb89fc78ac0e0720",
    25: "ae00970055066db7eed6c8d83898baa74feadb413729d286b24577d3335b9f5c",
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
