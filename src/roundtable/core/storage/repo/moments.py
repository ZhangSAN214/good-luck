"""朋友圈（只存本机）。

从 repository.py 拆出（代码精简）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from roundtable.core.storage.records import _chat_row, _moment_row, iso


class MomentsRepo:
    """朋友圈（只存本机）。"""

    # --- 朋友圈（docs/CHAT.md G5） --------------------------------------------------

    def add_moment(
        self,
        author: str,
        text: str,
        *,
        meta: dict[str, Any] | None = None,
        offline: bool = False,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        cur = self._exec(
            "INSERT INTO moments (author, text, meta, offline, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                author,
                text,
                json.dumps(meta, ensure_ascii=False) if meta else None,
                int(offline),
                created_at or self.clock(),
            ),
        )
        return self.moment(cur.lastrowid) or {}

    def set_moment_meta(self, moment_id: int, meta: dict[str, Any] | None) -> None:
        """更新一条动态的附加信息（如感官整理出的感知卡）；正文不变。"""
        self._exec(
            "UPDATE moments SET meta = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False) if meta else None, moment_id),
        )

    def moment(self, moment_id: int) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM moments WHERE id = ?", (moment_id,)).fetchone()
        return self._with_reactions([_moment_row(row)])[0] if row else None

    def moments(
        self,
        *,
        author: str | None = None,
        before: int | None = None,
        since: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """新的在前，带点赞（who 列表）和评论（按先后）。before：只要 id 更小的（翻页）。"""
        sql, params = "SELECT * FROM moments WHERE 1 = 1", []
        if author:
            sql += " AND author = ?"
            params.append(author)
        if before:
            sql += " AND id < ?"
            params.append(before)
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = [_moment_row(r) for r in self._exec(sql, params).fetchall()]
        return self._with_reactions(rows)

    def _with_reactions(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            return rows
        ids = [r["id"] for r in rows]
        marks = ", ".join("?" for _ in ids)
        likes: dict[int, list[str]] = {}
        for r in self._exec(
            f"SELECT moment_id, who FROM moment_likes WHERE moment_id IN ({marks})"
            " ORDER BY created_at",
            ids,
        ).fetchall():
            likes.setdefault(r["moment_id"], []).append(r["who"])
        comments: dict[int, list[dict[str, Any]]] = {}
        for r in self._exec(
            f"SELECT * FROM moment_comments WHERE moment_id IN ({marks}) ORDER BY id", ids
        ).fetchall():
            c = dict(r)
            c["offline"] = bool(c["offline"])
            comments.setdefault(r["moment_id"], []).append(c)
        for row in rows:
            row["likes"] = likes.get(row["id"], [])
            row["comments"] = comments.get(row["id"], [])
        return rows

    def latest_moment_id(self) -> int:
        return self._exec("SELECT COALESCE(MAX(id), 0) FROM moments").fetchone()[0]

    def unseen_moments(self, after: int) -> int:
        """id 大于 after 的成员动态条数（用户自己发的不算）。"""
        return self._exec(
            "SELECT COUNT(*) FROM moments WHERE id > ? AND author != 'user'", (after,)
        ).fetchone()[0]

    def like_moment(self, moment_id: int, who: str) -> bool:
        cur = self._exec(
            "INSERT OR IGNORE INTO moment_likes (moment_id, who, created_at) VALUES (?, ?, ?)",
            (moment_id, who, self.clock()),
        )
        return cur.rowcount > 0

    def unlike_moment(self, moment_id: int, who: str) -> bool:
        cur = self._exec(
            "DELETE FROM moment_likes WHERE moment_id = ? AND who = ?", (moment_id, who)
        )
        return cur.rowcount > 0

    def add_moment_comment(
        self,
        moment_id: int,
        author: str,
        text: str,
        *,
        reply_to: int | None = None,
        offline: bool = False,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        cur = self._exec(
            "INSERT INTO moment_comments (moment_id, author, text, reply_to, offline, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (moment_id, author, text, reply_to, int(offline), created_at or self.clock()),
        )
        row = self._exec("SELECT * FROM moment_comments WHERE id = ?", (cur.lastrowid,)).fetchone()
        out = dict(row)
        out["offline"] = bool(out["offline"])
        return out

    def moment_comment(self, comment_id: int) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM moment_comments WHERE id = ?", (comment_id,)).fetchone()
        return dict(row) if row else None

    def delete_moment(self, moment_id: int) -> bool:
        return self._exec("DELETE FROM moments WHERE id = ?", (moment_id,)).rowcount > 0

    def delete_moment_comment(self, comment_id: int) -> bool:
        cur = self._exec("DELETE FROM moment_comments WHERE id = ?", (comment_id,))
        return cur.rowcount > 0

    def office_work_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """[start, end) 内办公室会话里员工办事的调用：模型、会话、题目、是否匿名未揭晓。

        生活引擎用来知道谁今天干了什么活（匿名未揭晓的会话由调用方跳过）。
        """
        rows = self._exec(
            "SELECT DISTINCT c.model_id, s.id AS session_id, s.question, s.anonymous,"
            " s.revealed_at, MAX(c.created_at) AS at FROM calls c JOIN sessions s"
            " ON s.id = c.session_id WHERE s.workflow = 'office'"
            " AND c.role IN ('member', 'coordinator')"
            " AND c.model_id IS NOT NULL AND c.created_at >= ? AND c.created_at < ?"
            " GROUP BY c.model_id, s.id ORDER BY at",
            (iso(start), iso(end)),
        ).fetchall()
        return [dict(r) for r in rows]

    def chat_messages_between(self, start: str, end: str) -> list[dict[str, Any]]:
        """[start, end) 内全部会话的消息（按时间先后），整理记忆用。"""
        rows = self._exec(
            "SELECT * FROM chat_messages WHERE created_at >= ? AND created_at < ? ORDER BY id",
            (start, end),
        ).fetchall()
        return [_chat_row(r) for r in rows]

    def first_chat_message_by_author(self) -> dict[str, str]:
        """每位发过言的人第一次说话的时间（作者 → ISO 时间），日历算入职纪念日用。"""
        rows = self._exec(
            "SELECT author, MIN(created_at) FROM chat_messages GROUP BY author"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def spent_in_workflow(self, workflow: str, start: datetime, end: datetime) -> float:
        """[start, end) 内挂在某类隐藏会话（如 workflow = 'chat'）上的调用花费。"""
        return self._exec(
            "SELECT COALESCE(SUM(c.cost_usd), 0) FROM calls c JOIN sessions s"
            " ON s.id = c.session_id WHERE s.workflow = ? AND c.created_at >= ?"
            " AND c.created_at < ?",
            (workflow, iso(start), iso(end)),
        ).fetchone()[0]

    def spent_on_channels(self, channels: Sequence[str], start: datetime, end: datetime) -> float:
        """[start, end) 内走这些渠道的调用花费（dsh 工作台从余额差值里扣掉圆桌自己用掉的）。"""
        names = list(channels)
        if not names:
            return 0.0
        marks = ",".join("?" * len(names))
        return self._exec(
            f"SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE channel IN ({marks})"
            " AND created_at >= ? AND created_at < ?",
            (*names, iso(start), iso(end)),
        ).fetchone()[0]

    def spent_in_role(self, role: str, start: datetime, end: datetime) -> float:
        """[start, end) 内某种调用角色（calls.role，如 'media'）的花费：创作输出的每月上限。"""
        return self._exec(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE role = ? AND created_at >= ?"
            " AND created_at < ?",
            (role, iso(start), iso(end)),
        ).fetchone()[0]

    def spent_in_step(self, step: str, start: datetime, end: datetime) -> float:
        """[start, end) 内某个步骤名（calls.step，如 'plain_rewrite'）的调用花费。"""
        return self._exec(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE step = ? AND created_at >= ?"
            " AND created_at < ?",
            (step, iso(start), iso(end)),
        ).fetchone()[0]

    def calls_in_workflow(self, workflow: str, start: datetime, end: datetime) -> int:
        """[start, end) 内挂在某类隐藏会话上的调用次数。"""
        return self._exec(
            "SELECT COUNT(*) FROM calls c JOIN sessions s ON s.id = c.session_id"
            " WHERE s.workflow = ? AND c.created_at >= ? AND c.created_at < ?",
            (workflow, iso(start), iso(end)),
        ).fetchone()[0]

    def chat_messages(
        self, room: str, *, after: int = 0, limit: int = 200, query: str | None = None
    ) -> list[dict[str, Any]]:
        """id 大于 after 的消息（按时间先后）；有 query 时只返回包含它的消息（最近的 limit 条）。"""
        sql = "SELECT * FROM chat_messages WHERE room = ? AND id > ?"
        params: list[Any] = [room, after]
        if query:
            sql += " AND instr(text, ?) > 0"
            params.append(query)
        rows = self._exec(
            f"SELECT * FROM ({sql} ORDER BY id DESC LIMIT ?) ORDER BY id", (*params, limit)
        ).fetchall()
        return [_chat_row(r) for r in rows]

    def set_chat_message_meta(self, message_id: int, meta: dict[str, Any] | None) -> None:
        """更新一条消息的附加信息（如感官整理出的感知卡）；文字本身不变。"""
        self._exec(
            "UPDATE chat_messages SET meta = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False) if meta else None, message_id),
        )

    def clear_chat(self, room: str) -> int:
        """一键清空：删掉聊天记录，并抹掉对应调用里存的消息原文（花费记录保留，预算照算）。"""
        session_id = self.chat_session(room)
        with self._tx():
            n = self.conn.execute("DELETE FROM chat_messages WHERE room = ?", (room,)).rowcount
            self.conn.execute(
                "UPDATE calls SET input = '[]', output = NULL WHERE session_id = ?", (session_id,)
            )
        return n

    def spent_by_model(self, start: datetime, end: datetime) -> dict[str, dict[str, float]]:
        """[start, end) 内按模型汇总的调用次数、token 与花费（办公室模式的"已领工资"）。"""
        rows = self._exec(
            "SELECT model_id, COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS tin,"
            " COALESCE(SUM(output_tokens), 0) AS tout, COALESCE(SUM(cost_usd), 0) AS cost"
            " FROM calls WHERE model_id IS NOT NULL AND created_at >= ? AND created_at < ?"
            " GROUP BY model_id",
            (iso(start), iso(end)),
        ).fetchall()
        return {
            r["model_id"]: {
                "calls": r["calls"],
                "input_tokens": r["tin"],
                "output_tokens": r["tout"],
                "cost_usd": r["cost"],
            }
            for r in rows
        }
