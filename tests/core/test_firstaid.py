"""「出问题点这里」自检：每类检查报对级别；和备份比数据量；报告去掉 key 和用户目录；服务里能用。"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta

from roundtable.core import firstaid as fa
from roundtable.core.backup import DbBackup
from roundtable.core.providers.secrets import Secret
from roundtable.core.service import RoundtableService
from roundtable.core.storage import connect

from .orchestrator.conftest import Env

NOW = datetime(2026, 11, 2, 9, 0)


def levels(found):
    return {f.id: f.level for f in found}


def test_summary_orders_worst_first_and_groups_scenes():
    found = [
        fa.Finding("a", fa.START, fa.OK, "好"),
        fa.Finding("b", fa.DATA, fa.BAD, "坏"),
        fa.Finding("c", fa.HANG, fa.WARN, "留意"),
    ]
    s = fa.summarize(found)
    assert s["level"] == fa.BAD and "2 个问题" in s["headline"]
    assert [f["id"] for f in s["findings"]] == ["b", "c", "a"]
    assert {x["id"]: x["level"] for x in s["scenes"]} == {
        "start": "ok",
        "hang": "warn",
        "undo": "ok",
        "data": "bad",
    }
    assert fa.summarize([fa.Finding("a", fa.START, fa.OK, "好")])["headline"] == "一切正常"


def test_database_checks(tmp_path):
    db = tmp_path / "rt.db"
    assert levels(fa.check_database(None, db)) == {"db": "info"}  # 还没有文件
    db.write_bytes(b"not a database at all" * 100)
    assert levels(fa.check_database(fa.open_readonly(db), db)) == {"db": "bad"}
    db.unlink()
    connect(db).close()
    conn = fa.open_readonly(db)
    assert levels(fa.check_database(conn, db)) == {"db": "ok", "db_version": "ok"}
    conn.close()
    # 数据库比程序新（退回了旧代码）
    raw = sqlite3.connect(db)
    raw.execute("INSERT INTO schema_version VALUES (99999, 'future', 'x')")
    raw.commit()
    raw.close()
    conn = fa.open_readonly(db)
    got = {f.id: f for f in fa.check_database(conn, db)}
    conn.close()
    assert got["db_version"].level == "bad" and "99999" in got["db_version"].detail
    assert levels(fa.check_database(None, None)) == {"db": "info"}  # 内存数据库


def test_keys_only_report_names_never_values():
    assert levels(fa.check_keys([], ["A_KEY"])) == {"keys": "bad"}
    assert levels(fa.check_keys([], ["A_KEY"], channels_ok=True)) == {"keys": "info"}
    got = fa.check_keys(["A_KEY"], ["B_KEY"])[0]
    assert got.level == "ok" and "A_KEY" in got.detail and "B_KEY" in got.detail


def test_backup_age_and_data_drop(tmp_path):
    assert levels(fa.check_backups([], True, NOW)) == {"backup": "warn"}
    fresh = [{"at": (NOW - timedelta(hours=5)).isoformat(), "name": "x"}]
    old = [{"at": (NOW - timedelta(days=9)).isoformat(), "name": "x"}]
    assert levels(fa.check_backups(fresh, True, NOW)) == {"backup": "ok"}
    assert levels(fa.check_backups(old, False, NOW)) == {"backup": "warn"}

    db = tmp_path / "rt.db"
    conn = connect(db)
    for i in range(100):
        conn.execute(
            "INSERT INTO chat_messages (room, author, text, created_at) VALUES ('r', 'u', ?, 'x')",
            (f"m{i}",),
        )
    box = DbBackup(conn, tmp_path / "bk")
    box.daily(NOW)
    conn.execute("DELETE FROM chat_messages WHERE id > 30")
    files = fa.list_backups(tmp_path / "bk")
    assert len(files) == 1
    ro = fa.open_readonly(db)
    got = fa.compare_with_backup(ro, tmp_path / "bk", files)
    ro.close()
    assert got[0].level == "bad" and "100 → 30" in got[0].detail
    # 少一点点不报
    assert levels(fa.check_data_drop({"chat_messages": 95}, {"chat_messages": 100}, "b")) == {
        "data_drop": "ok"
    }
    conn.close()


def test_update_record_scenes():
    assert levels(fa.check_update(None, NOW)) == {"update": "info"}
    ok = {"phase": "ok", "from_commit": "a" * 40, "to_commit": "b" * 40, "finished_at": "x"}
    got = fa.check_update(ok, NOW)[0]
    assert got.level == "ok" and "undo_update.bat" in got.fix
    back = {"phase": "rolled_back", "from_commit": "a" * 40, "reason": "导入失败"}
    assert fa.check_update(back, NOW)[0].level == "warn"
    stuck = {"phase": "begun", "started_at": (NOW - timedelta(hours=2)).isoformat()}
    assert fa.check_update(stuck, NOW)[0].title == "上次更新中途断了"
    running = {"phase": "begun", "started_at": (NOW - timedelta(minutes=2)).isoformat()}
    assert fa.check_update(running, NOW)[0].level == "info"
    assert fa.check_update({"phase": "undone"}, NOW)[0].level == "info"
    assert fa.check_update({"phase": "failed", "reason": "x"}, NOW)[0].level == "warn"


def test_install_record():
    assert levels(fa.check_install(None)) == {"install": "info"}
    rec = {
        "start": {"at": "t", "items": [{"id": "a", "ok": True, "title": "A"}]},
        "dsh": {
            "at": "t",
            "items": [
                {"id": "b", "ok": False, "level": "bad", "title": "B", "detail": "d", "fix": "f"}
            ],
        },
    }
    got = {f.id: f for f in fa.check_install(rec)}
    assert got["install_start"].level == "ok"
    assert got["install_dsh"].level == "bad" and "B：d" in got["install_dsh"].detail


def test_live_checks():
    async def boom():
        raise RuntimeError("出错了")

    async def forever():
        await asyncio.sleep(3600)

    async def main():
        bad = asyncio.ensure_future(boom())
        alive = asyncio.ensure_future(forever())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        got = levels(fa.check_tasks({"生活": bad, "备份": alive, "没开": None}))
        alive.cancel()
        return got

    assert asyncio.run(main()) == {"task_生活": "bad"}
    assert levels(fa.check_channels({"x": {"available": False, "reason": "缺 key"}})) == {
        "channels": "bad"
    }
    assert levels(fa.check_channels({"x": {"available": False}, "y": {"available": True}})) == {
        "channels": "info"
    }
    full = {"spent_usd": 5, "limit_usd": 5, "exhausted": True, "warn": True}
    assert levels(fa.check_budget(full, None)) == {"budget_本月": "bad"}
    assert levels(fa.check_calls(10, 6)) == {"calls": "bad"}
    assert levels(fa.check_calls(10, 2)) == {"calls": "warn"}
    assert levels(fa.check_calls(10, 0)) == {"calls": "ok"}
    assert levels(fa.check_sandbox("没装")) == {"sandbox": "warn"}
    assert fa.check_dsh({"enabled": False}) == []
    assert levels(fa.check_dsh({"enabled": True, "installed": False})) == {"dsh": "info"}


def test_report_strips_keys_and_home_dir():
    fake = "sk-or-" + "v1-" + "0123456789abcdef" * 3
    Secret(fake)  # 已加载的 key 会登记到脱敏表
    found = [
        fa.Finding("x", fa.HANG, fa.BAD, "后台出错", f"401 {fake} at /home/someone/rt/data", "重启")
    ]
    text = fa.render_report(fa.summarize(found), fa.meta_info("3.1.0", NOW), home="/home/someone")
    assert fake not in text and "/home/someone" not in text
    assert "~/rt/data" in text and "怎么办：重启" in text and "3.1.0" in text


def test_offline_reads_update_and_install_records(tmp_path):
    (tmp_path / fa.UPDATE_STATE).write_text(json.dumps({"phase": "rolled_back"}), "utf-8")
    (tmp_path / fa.INSTALL_CHECK).write_text("{bad json", "utf-8")
    found = fa.offline_findings(
        db_path=tmp_path / "rt.db", data=tmp_path, config_error="config/x.yaml: a.b 多余", now=NOW
    )
    got = levels(found)
    assert got["config"] == "bad" and "keys" not in got  # 配置读不了就不看 key
    assert got["update"] == "warn" and got["install"] == "info"
    assert "backup" not in got  # 还没有数据库文件时不催备份


def test_service_firstaid_and_report():
    svc = RoundtableService(Env().rt)
    out = svc.firstaid()
    assert out["level"] in ("ok", "info", "warn", "bad")
    ids = {f["id"] for f in out["findings"]}
    assert {"config", "keys", "db", "channels", "budget", "calls", "sandbox"} <= ids
    report = svc.firstaid_report()
    assert report["text"].startswith("# 圆桌体检报告")
