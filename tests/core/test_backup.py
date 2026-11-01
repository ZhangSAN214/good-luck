"""整库备份：每天一份只备一次、按份数清理、存档点另算、恢复前校验并自动存「恢复前」、
坏文件 / 版本太新 / 不认识的名字都不动现在的数据库。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from roundtable.core.backup import (
    BEFORE_RESTORE,
    CHECKPOINT,
    DAILY,
    BackupError,
    DbBackup,
    clean_label,
    parse_name,
)
from roundtable.core.storage.db import connect
from roundtable.core.storage.migrations import MIGRATIONS

T0 = datetime(2026, 11, 2, 9, 30)


def _db(tmp_path):
    conn = connect(tmp_path / "live" / "roundtable.db")
    conn.execute("CREATE TABLE IF NOT EXISTS t_backup_probe (v TEXT)")
    return conn


def _put(conn, v):
    conn.execute("DELETE FROM t_backup_probe")
    conn.execute("INSERT INTO t_backup_probe VALUES (?)", (v,))


def _get(conn):
    return conn.execute("SELECT v FROM t_backup_probe").fetchone()[0]


def test_daily_once_per_day_and_prune(tmp_path):
    conn = _db(tmp_path)
    box = DbBackup(conn, tmp_path / "bk", keep_daily=3)
    assert box.daily(T0) is not None
    assert box.daily(T0 + timedelta(hours=5)) is None  # 同一天不再备
    for d in range(1, 6):
        box.daily(T0 + timedelta(days=d))
    daily = [f for f in box.files() if f.kind == DAILY]
    assert len(daily) == 3 and daily[0].at.date() == (T0 + timedelta(days=5)).date()
    assert not list((tmp_path / "bk").glob("*.part"))


def test_disabled_or_memory_does_nothing(tmp_path):
    conn = _db(tmp_path)
    assert DbBackup(conn, tmp_path / "bk", enabled=False).daily(T0) is None
    mem = DbBackup(connect(), None)
    assert mem.daily(T0) is None and mem.files() == [] and not mem.status()["enabled"]
    with pytest.raises(BackupError):
        mem.checkpoint("x", T0)


def test_checkpoints_counted_separately(tmp_path):
    conn = _db(tmp_path)
    box = DbBackup(conn, tmp_path / "bk", keep_daily=1, keep_checkpoints=2)
    box.daily(T0)
    for i in range(4):
        box.checkpoint(f"试跑前{i}", T0 + timedelta(minutes=i))
    kinds = [f.kind for f in box.files()]
    assert kinds.count(CHECKPOINT) == 2 and kinds.count(DAILY) == 1
    assert box.files()[0].label == "试跑前3"


def test_label_and_name_parsing():
    assert clean_label("  ../../etc/passwd ") == "etc-passwd"
    assert clean_label("") == "手动" and clean_label("!!!") == "手动"
    assert len(clean_label("长" * 50)) == 24
    assert parse_name("../roundtable-daily-20261102.db") is None
    assert parse_name("roundtable-daily-20261102.db").kind == DAILY
    assert parse_name("roundtable-checkpoint-20261102-093000-更新前.db").label == "更新前"


def test_restore_roundtrip_with_before_point(tmp_path):
    conn = _db(tmp_path)
    box = DbBackup(conn, tmp_path / "bk")
    _put(conn, "周一")
    old = box.daily(T0)
    _put(conn, "周二被误删了")
    out = box.restore(old.name, T0 + timedelta(days=1))
    assert _get(conn) == "周一" and out["restart"]
    assert out["before"]["label"] == BEFORE_RESTORE
    # 反悔：恢复「恢复前」那份
    box.restore(out["before"]["name"], T0 + timedelta(days=1, minutes=1))
    assert _get(conn) == "周二被误删了"


def test_restore_refuses_bad_files(tmp_path):
    conn = _db(tmp_path)
    folder = tmp_path / "bk"
    box = DbBackup(conn, folder)
    _put(conn, "原样")
    with pytest.raises(BackupError):
        box.restore("../live/roundtable.db", T0)
    with pytest.raises(BackupError):
        box.restore("roundtable-daily-20990101.db", T0)  # 不存在
    folder.mkdir(exist_ok=True)
    (folder / "roundtable-daily-20261101.db").write_bytes(b"not a database" * 100)
    with pytest.raises(BackupError):
        box.restore("roundtable-daily-20261101.db", T0)
    other = sqlite3.connect(folder / "roundtable-daily-20261031.db")
    other.execute("CREATE TABLE x (a)")
    other.close()
    with pytest.raises(BackupError, match="不是圆桌"):
        box.restore("roundtable-daily-20261031.db", T0)
    newer = box.daily(T0)
    db = sqlite3.connect(folder / newer.name)
    db.execute(
        "INSERT INTO schema_version VALUES (?, 'future', datetime('now'))",
        (MIGRATIONS[-1][0] + 1,),
    )
    db.commit()
    db.close()
    with pytest.raises(BackupError, match="新"):
        box.restore(newer.name, T0)
    assert _get(conn) == "原样"
    assert not [f for f in box.files() if f.label == BEFORE_RESTORE]  # 校验没过就不存「恢复前」
