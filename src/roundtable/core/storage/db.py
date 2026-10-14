"""SQLite 连接与迁移。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .migrations import MIGRATIONS

MEMORY = ":memory:"


class MigrationError(RuntimeError):
    """数据库版本比代码新，或迁移失败。"""


def connect(path: str | Path = MEMORY) -> sqlite3.Connection:
    """打开数据库并应用迁移。文件所在目录不存在时自动创建。"""
    if str(path) != MEMORY:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if str(path) != MEMORY:
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection) -> int:
    """应用尚未执行的迁移，返回当前版本。可重复调用。"""
    version = current_version(conn)
    latest = MIGRATIONS[-1][0] if MIGRATIONS else 0
    if version > latest:
        raise MigrationError(f"数据库版本 {version} 比代码支持的 {latest} 新，请升级程序")
    for number, name, sql in MIGRATIONS:
        if number <= version:
            continue
        try:
            conn.execute("BEGIN")
            for statement in _statements(sql):
                conn.execute(statement)
            conn.execute(
                "INSERT INTO schema_version (version, name, applied_at) "
                "VALUES (?, ?, datetime('now'))",
                (number, name),
            )
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            conn.execute("ROLLBACK")
            raise MigrationError(f"迁移 {number}（{name}）失败：{exc}") from exc
        version = number
    return version


def _statements(sql: str) -> list[str]:
    """按分号拆分语句（迁移脚本里不使用触发器等含分号的语句体）。"""
    lines = [line.split("--", 1)[0] for line in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
