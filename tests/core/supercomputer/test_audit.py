"""哈希链审计：追加、重开接着写、改一条 / 删一条 / 调换 / 整条重写都能查出来、脱敏。"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from roundtable.core.supercomputer.audit import GENESIS, AuditLog, digest

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        self.t += timedelta(seconds=1)
        return self.t


def lines(path):
    return path.read_text(encoding="utf-8").splitlines()


def test_chain_and_reopen(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, now=Clock())
    a = log.append("submit", task="t1", text="加接口")
    b = log.append("finish", task="t1", status="done")
    assert a["prev"] == GENESIS and b["prev"] == a["hash"] and b["n"] == 2
    assert a["hash"] == digest(1, a["at"], "submit", a["data"], GENESIS)
    again = AuditLog(path, now=Clock())  # 重开：接着上一条写
    assert again.count == 2 and again.head == b["hash"]
    c = again.append("wake", worker="k")
    assert c["n"] == 3 and c["prev"] == b["hash"]
    assert again.verify() == {
        "ok": True,
        "count": 3,
        "head": c["hash"],
        "broken_at": None,
        "why": None,
    }


def tamper(path, i, fn):
    rows = lines(path)
    e = json.loads(rows[i])
    fn(e)
    rows[i] = json.dumps(e, ensure_ascii=False)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def fresh(tmp_path, n=4):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path, now=Clock())
    for k in range(n):
        log.append("submit", task=f"t{k}")
    return path, log


def test_edit_is_caught(tmp_path):
    path, log = fresh(tmp_path)
    tamper(path, 1, lambda e: e["data"].update(task="别的"))
    got = log.verify()
    assert not got["ok"] and got["broken_at"] == 2 and "被改过" in got["why"]


def test_delete_and_swap_are_caught(tmp_path):
    path, log = fresh(tmp_path)
    rows = lines(path)
    path.write_text("\n".join([rows[0], rows[2], rows[3]]) + "\n", encoding="utf-8")
    got = log.verify()
    assert not got["ok"] and got["broken_at"] == 2
    path, log = fresh(tmp_path / "b")
    rows = lines(path)
    path.write_text("\n".join([rows[1], rows[0], *rows[2:]]) + "\n", encoding="utf-8")
    assert log.verify()["broken_at"] == 1


def test_broken_line(tmp_path):
    path, log = fresh(tmp_path)
    with path.open("a", encoding="utf-8") as f:
        f.write("{坏的\n")
    got = log.verify()
    assert not got["ok"] and got["broken_at"] == 5 and "不是完整" in got["why"]


def test_rewritten_chain_is_caught_by_anchor(tmp_path):
    """整条重算一遍能骗过哈希链，骗不过日报记下的锚点。"""
    path, log = fresh(tmp_path)
    anchor = {"2026-11-01": {"n": 3, "hash": json.loads(lines(path)[2])["hash"]}}
    assert log.verify(anchor)["ok"]
    path.unlink()
    forged = AuditLog(path, now=Clock())
    for k in range(4):
        forged.append("submit", task=f"假{k}")
    assert forged.verify()["ok"]  # 只看链：看不出来
    got = forged.verify(anchor)
    assert not got["ok"] and got["broken_at"] == 3 and "锚点" in got["why"]
    short = AuditLog(tmp_path / "s.jsonl", now=Clock())
    short.append("x")
    assert "只剩 1 条" in short.verify(anchor)["why"]


def test_redacts_and_trims(tmp_path):
    key = "sk-or-v1-" + "a1b2c3d4" * 6
    log = AuditLog(tmp_path / "a.jsonl", now=Clock())
    log.append("submit", text=f"用这个 {key} 跑一下" + "长" * 900, extra=[key])
    raw = (tmp_path / "a.jsonl").read_text(encoding="utf-8")
    assert key not in raw and "***" in raw
    assert len(log.entries()[0]["data"]["text"]) <= 500
    assert log.verify()["ok"]


def test_memory_only():
    log = AuditLog(None, now=Clock())
    log.append("submit", task="t")
    log.append("finish", task="t")
    assert log.verify()["ok"] and len(log.entries()) == 2
    assert len(log.since(T0, T0 + timedelta(minutes=1))) == 2
