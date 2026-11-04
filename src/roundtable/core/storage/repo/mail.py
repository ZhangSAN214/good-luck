"""终端邮件栏：用户本人学校邮箱的只读缓存。

从 repository.py 拆出（代码精简）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from roundtable.core.storage.records import _mail_row


class MailRepo:
    """终端邮件栏：用户本人学校邮箱的只读缓存。"""

    # --- 终端邮件栏（用户本人学校邮箱的只读缓存，docs/MAIL.md） --------------------------

    def upsert_mail(self, items: Sequence[Mapping[str, Any]]) -> list[str]:
        """存邮件列表；已有的更新状态（已读、旗标……），正文保留。返回新加的 id。"""
        added: list[str] = []
        now = self.clock()
        cols = ("sender_name", "sender_addr", "subject", "preview")
        flags = ("is_read", "flagged", "high", "attachments")
        with self._tx():
            for m in items:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO mail_messages (id, received_at, sender_name,"
                    " sender_addr, subject, preview, is_read, flagged, high, attachments,"
                    " fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        m["id"],
                        m["received_at"],
                        *(m.get(c, "") for c in cols),
                        *(int(bool(m.get(f))) for f in flags),
                        now,
                    ),
                )
                if cur.rowcount:
                    added.append(m["id"])
                    continue
                self.conn.execute(
                    "UPDATE mail_messages SET received_at = ?, sender_name = ?, sender_addr = ?,"
                    " subject = ?, preview = ?, is_read = ?, flagged = ?, high = ?,"
                    " attachments = ?, fetched_at = ? WHERE id = ?",
                    (
                        m["received_at"],
                        *(m.get(c, "") for c in cols),
                        *(int(bool(m.get(f))) for f in flags),
                        now,
                        m["id"],
                    ),
                )
        return added

    def mail_messages(self, since: str, limit: int = 200) -> list[dict[str, Any]]:
        """since 之后收到的，新的在前（不含正文）。"""
        rows = self._exec(
            "SELECT id, received_at, sender_name, sender_addr, subject, preview, is_read,"
            " flagged, high, attachments, mark, category, body IS NOT NULL AS has_body,"
            " summary IS NOT NULL AS has_summary, translation IS NOT NULL AS has_translation,"
            " html IS NOT NULL AS has_html FROM mail_messages"
            " WHERE received_at >= ? ORDER BY received_at DESC, id LIMIT ?",
            (since, limit),
        ).fetchall()
        return [_mail_row(r) for r in rows]

    def mail_message(self, message_id: str) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM mail_messages WHERE id = ?", (message_id,)).fetchone()
        return _mail_row(row) if row else None

    def set_mail_bodies(self, bodies: Mapping[str, str]) -> None:
        with self._tx():
            for message_id, body in bodies.items():
                self.conn.execute(
                    "UPDATE mail_messages SET body = ? WHERE id = ?", (body, message_id)
                )

    MAIL_FIELDS = frozenset({"mark", "category", "summary", "translation", "html"})

    def set_mail_fields(self, message_id: str, **fields: str | None) -> bool:
        """改一封邮件的本机字段（标记、分类、总结、翻译、图片版正文）；不写回邮箱。"""
        bad = set(fields) - self.MAIL_FIELDS
        if bad or not fields:
            raise ValueError(f"不能改这些字段：{sorted(bad)}")
        cols = ", ".join(f"{k} = ?" for k in fields)
        cur = self._exec(
            f"UPDATE mail_messages SET {cols} WHERE id = ?", (*fields.values(), message_id)
        )
        return cur.rowcount > 0

    def mail_without_body(self, ids: Sequence[str]) -> list[str]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self._exec(
            f"SELECT id FROM mail_messages WHERE body IS NULL AND id IN ({marks})", tuple(ids)
        ).fetchall()
        return [r["id"] for r in rows]

    def prune_mail(self, before: str) -> int:
        """删掉 before 之前收到的（提醒随邮件一起删）。"""
        return self._exec("DELETE FROM mail_messages WHERE received_at < ?", (before,)).rowcount

    def clear_mail(self) -> int:
        """清空邮件缓存与提醒（审计记录保留，便于查看做过什么）。

        AI 总结 / 翻译的调用记录只留花费（计预算用），发出去的邮件内容和回来的文字一并抹掉。"""
        with self._tx():
            self.conn.execute(
                "UPDATE calls SET input = '[]', output = NULL WHERE session_id IN"
                " (SELECT id FROM sessions WHERE workflow = 'mail')"
            )
            self.conn.execute("DELETE FROM mail_reminders")
            return self.conn.execute("DELETE FROM mail_messages").rowcount

    def add_mail_reminder(self, message_id: str, due: str, snippet: str) -> bool:
        cur = self._exec(
            "INSERT OR IGNORE INTO mail_reminders (message_id, due, snippet, created_at)"
            " VALUES (?, ?, ?, ?)",
            (message_id, due, snippet, self.clock()),
        )
        return cur.rowcount > 0

    def mail_reminders(self, statuses: Sequence[str] = ("pending", "accepted")) -> list[dict]:
        marks = ",".join("?" * len(statuses))
        rows = self._exec(
            "SELECT r.*, m.subject, m.sender_name FROM mail_reminders r"
            " JOIN mail_messages m ON m.id = r.message_id"
            f" WHERE r.status IN ({marks}) ORDER BY r.due, r.id",
            tuple(statuses),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_mail_reminder(self, reminder_id: int, status: str) -> bool:
        cur = self._exec("UPDATE mail_reminders SET status = ? WHERE id = ?", (status, reminder_id))
        return cur.rowcount > 0

    def add_mail_audit(self, action: str, ok: bool, detail: str = "") -> None:
        self._exec(
            "INSERT INTO mail_audit (at, action, ok, detail) VALUES (?, ?, ?, ?)",
            (self.clock(), action, int(ok), detail[:300]),
        )

    def mail_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM mail_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{**dict(r), "ok": bool(r["ok"])} for r in rows]
