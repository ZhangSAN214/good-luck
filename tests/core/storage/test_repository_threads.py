"""多线程共用一条连接：结果要在锁内取完，别的线程同时写或开事务时不能读到 None 或别的行。

CI 上浏览器测试偶发 KeyError: 'handoff'（测试线程读交办消息，服务线程同时在写）就是这个原因。
"""

from __future__ import annotations

import threading
import time

from roundtable.core.storage.db import connect
from roundtable.core.storage.repository import Repository


def test_reads_stay_correct_while_another_thread_writes(tmp_path):
    repo = Repository(connect(tmp_path / "t.db"))
    ids = [
        repo.add_chat_message("main", f"V{i}", f"t{i}", meta={"k": i, "handoff": {"s": "p"}})["id"]
        for i in range(30)
    ]
    bad: list[object] = []
    stop = time.monotonic() + 1.5

    def reader(part: list[int]) -> None:
        while time.monotonic() < stop and not bad:
            for mid in part:
                row = repo.chat_message(mid)
                if row is None or row["id"] != mid or "handoff" not in (row["meta"] or {}):
                    bad.append((mid, row and row["id"]))
                    return

    def writer() -> None:
        i = 0
        while time.monotonic() < stop and not bad:
            m = repo.add_chat_message("other", "W", "x", meta={"w": i})
            if m is None or m["room"] != "other":
                bad.append(("insert", m))
                return
            repo.set_chat_message_meta(m["id"], {"w": i, "x": 1})
            with repo._tx():
                repo.conn.execute("UPDATE chat_messages SET text = text WHERE id = ?", (m["id"],))
            i += 1

    threads = [threading.Thread(target=reader, args=(ids[k::3],)) for k in range(3)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert bad == []


def test_exec_result_behaves_like_a_cursor(tmp_path):
    repo = Repository(connect(tmp_path / "t.db"))
    a = repo.add_chat_message("main", "V1", "一")
    b = repo.add_chat_message("main", "V2", "二")
    res = repo._exec("SELECT id FROM chat_messages ORDER BY id")
    assert res.fetchone()[0] == a["id"]
    assert [r[0] for r in res] == [b["id"]]
    assert res.fetchone() is None and res.fetchall() == []
    assert repo._exec("DELETE FROM chat_messages WHERE id = ?", (a["id"],)).rowcount == 1
    cur = repo._exec(
        "INSERT INTO chat_messages (room, author, text, created_at)"
        " VALUES ('main', 'V3', '三', 'x')"
    )
    assert cur.lastrowid == b["id"] + 1
