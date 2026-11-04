"""群聊、成员人设与记忆、记忆与联想、收得住、成员引擎、上网看到的内容（只存本机）。

从 repository.py 拆出（代码精简）。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from roundtable.core.storage.records import (
    _chat_row,
    _json,
    _life_row,
    _memory_row,
    _room_row,
    _web_row,
    iso,
)


class ChatRepo:
    """群聊、成员人设与记忆、记忆与联想、收得住、成员引擎、上网看到的内容（只存本机）。"""

    # --- 群聊 ------------------------------------------------------------------

    def chat_session(self, room: str) -> str:
        """群聊对应的隐藏会话（workflow = 'chat'）：成员发言的调用挂在这里，计入预算和工资。"""
        question = f"群聊：{room}"
        row = self._exec(
            "SELECT id FROM sessions WHERE workflow = 'chat' AND question = ? LIMIT 1", (question,)
        ).fetchone()
        if row:
            return row["id"]
        return self.create_session(question, seed=0, anonymous=False, workflow="chat")

    def scout_session(self) -> str:
        """革新例会自己找资料的隐藏会话（workflow = 'evolution_scout'）：搜索、写卡、复核的调用
        挂在这里，计入总预算和革新例会的每月上限；不出现在会话列表里。"""
        question = "革新例会：自己找资料"
        row = self._exec(
            "SELECT id FROM sessions WHERE workflow = 'evolution_scout' LIMIT 1"
        ).fetchone()
        if row:
            return row["id"]
        return self.create_session(question, seed=0, anonymous=False, workflow="evolution_scout")

    def mail_session(self) -> str:
        """邮件栏 AI 总结 / 翻译的隐藏会话（workflow = 'mail'）：调用挂在这里，计入总预算和
        邮件栏的每月上限；不出现在会话列表里。发给模型的邮件内容只存在 calls 表里（本机）。"""
        row = self._exec("SELECT id FROM sessions WHERE workflow = 'mail' LIMIT 1").fetchone()
        if row:
            return row["id"]
        return self.create_session("邮件栏：总结与翻译", seed=0, anonymous=False, workflow="mail")

    def voice_session(self) -> str:
        """成员固定音色的隐藏会话（workflow = 'voice'）：用成员音色念文字的调用挂在这里，
        计入总预算和创作输出的每月上限（role = media）；不出现在会话列表里。"""
        row = self._exec("SELECT id FROM sessions WHERE workflow = 'voice' LIMIT 1").fetchone()
        if row:
            return row["id"]
        return self.create_session("成员音色：念给你听", seed=0, anonymous=False, workflow="voice")

    def sticker_session(self) -> str:
        """成员表情包的隐藏会话（workflow = 'sticker'）：画表情和核对配文的调用挂在这里，
        计入总预算、创作输出的每月上限和表情包的每月小上限（role = media）；不出现在会话列表里。"""
        row = self._exec("SELECT id FROM sessions WHERE workflow = 'sticker' LIMIT 1").fetchone()
        if row:
            return row["id"]
        return self.create_session("成员表情包", seed=0, anonymous=False, workflow="sticker")

    def watch_session(self) -> str:
        """投屏陪伴的隐藏会话（workflow = 'watch'）：画面转文字和成员陪看发言的调用挂在这里，
        计入总预算和「一起看」的每月上限；不出现在会话列表里。截图本身不存，只存描述的哈希。"""
        row = self._exec("SELECT id FROM sessions WHERE workflow = 'watch' LIMIT 1").fetchone()
        if row:
            return row["id"]
        return self.create_session("投屏陪伴：一起看", seed=0, anonymous=False, workflow="watch")

    def add_chat_message(
        self,
        room: str,
        author: str,
        text: str,
        *,
        model_id: str | None = None,
        reply_to: int | None = None,
        cost_usd: float = 0.0,
        meta: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """created_at 为空时用当前时间（补算「你不在时」的消息时按事件时刻写入）。"""
        cur = self._exec(
            "INSERT INTO chat_messages (room, author, model_id, text, reply_to, cost_usd,"
            " meta, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                room,
                author,
                model_id,
                text,
                reply_to,
                cost_usd,
                json.dumps(meta, ensure_ascii=False) if meta else None,
                created_at or self.clock(),
            ),
        )
        row = self._exec("SELECT * FROM chat_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _chat_row(row)

    def chat_message(self, message_id: int) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
        return _chat_row(row) if row else None

    def recall_chat_message(self, message_id: int) -> dict[str, Any] | None:
        """撤回：清掉文字和附件清单，只留「已撤回」标记（时间、作者不变）。"""
        row = self.chat_message(message_id)
        if row is None:
            return None
        meta = {
            k: v for k, v in (row["meta"] or {}).items() if k not in ("media", "caption", "quote")
        }
        meta["recalled"] = True
        self._exec(
            "UPDATE chat_messages SET text = '', meta = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), message_id),
        )
        return self.chat_message(message_id)

    def ensure_chat_room(
        self,
        room: str,
        *,
        kind: str,
        title: str,
        members: Sequence[str] = (),
        with_user: bool = True,
    ) -> dict[str, Any]:
        """没有就建；已有的不改（标题等以第一次建立时为准）。"""
        self._exec(
            "INSERT OR IGNORE INTO chat_rooms (id, kind, title, members, with_user, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (room, kind, title, json.dumps(list(members)), int(with_user), self.clock()),
        )
        return self.chat_room(room) or {}

    def chat_room(self, room: str) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM chat_rooms WHERE id = ?", (room,)).fetchone()
        return _room_row(row) if row else None

    def chat_rooms(self) -> list[dict[str, Any]]:
        """全部会话，带未读数和最后一条消息。

        未读 = last_read 之后成员发的消息，以及带 meta 的系统消息（如工作群里的交付通知）。
        """
        rows = self._exec(
            "SELECT r.*,"
            " (SELECT COUNT(*) FROM chat_messages m WHERE m.room = r.id AND m.id > r.last_read"
            "  AND m.author != 'user' AND (m.author != 'system' OR m.meta IS NOT NULL)) AS unread,"
            " (SELECT MAX(id) FROM chat_messages m WHERE m.room = r.id) AS last_id"
            " FROM chat_rooms r ORDER BY r.created_at, r.id"
        ).fetchall()
        out = []
        for r in rows:
            room = _room_row(r)
            room["unread"] = r["unread"]
            last = None
            if r["last_id"]:
                row = self._exec("SELECT * FROM chat_messages WHERE id = ?", (r["last_id"],))
                last = _chat_row(row.fetchone())
            room["last"] = last
            out.append(room)
        return out

    def mark_chat_read(self, room: str, upto: int) -> None:
        self._exec("UPDATE chat_rooms SET last_read = MAX(last_read, ?) WHERE id = ?", (upto, room))

    # --- 成员人设与记忆（聊天用，只存本地） ---------------------------------------

    def member_persona(self, member: str) -> dict[str, Any] | None:
        row = self._exec(
            "SELECT persona FROM member_personas WHERE member = ?", (member,)
        ).fetchone()
        return json.loads(row["persona"]) if row else None

    def set_member_persona(self, member: str, persona: Mapping[str, Any] | None) -> None:
        """persona 为 None 时删掉本地设定（回到配置文件里的）。"""
        if persona is None:
            self._exec("DELETE FROM member_personas WHERE member = ?", (member,))
            return
        self._exec(
            "INSERT INTO member_personas (member, persona, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(member) DO UPDATE SET persona = excluded.persona,"
            " updated_at = excluded.updated_at",
            (member, json.dumps(dict(persona), ensure_ascii=False), self.clock()),
        )

    def add_memory(
        self,
        member: str,
        layer: str,
        text: str,
        *,
        period: str = "",
        source: str = "auto",
        offline: bool = False,
    ) -> dict[str, Any]:
        now = self.clock()
        cur = self._exec(
            "INSERT INTO member_memories (member, layer, period, text, source, offline,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (member, layer, period, text, source, int(offline), now, now),
        )
        return self.memory(cur.lastrowid) or {}

    def memory(self, memory_id: int) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM member_memories WHERE id = ?", (memory_id,)).fetchone()
        return _memory_row(row) if row else None

    def memories(
        self, member: str | None = None, *, layer: str | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        """最新的在前。"""
        sql = "SELECT * FROM member_memories WHERE 1 = 1"
        params: list[Any] = []
        if member is not None:
            sql += " AND member = ?"
            params.append(member)
        if layer is not None:
            sql += " AND layer = ?"
            params.append(layer)
        sql += " ORDER BY period DESC, id DESC LIMIT ?"
        rows = self._exec(sql, (*params, limit)).fetchall()
        return [_memory_row(r) for r in rows]

    def update_memory(self, memory_id: int, text: str) -> dict[str, Any] | None:
        """用户改过的记忆标为 user，之后自动整理不会覆盖。"""
        self._exec(
            "UPDATE member_memories SET text = ?, source = 'user', updated_at = ? WHERE id = ?",
            (text, self.clock(), memory_id),
        )
        return self.memory(memory_id)

    def delete_memory(self, memory_id: int) -> bool:
        return self._exec("DELETE FROM member_memories WHERE id = ?", (memory_id,)).rowcount > 0

    def clear_memories(self, member: str | None = None) -> int:
        if member is None:
            return self._exec("DELETE FROM member_memories").rowcount
        return self._exec("DELETE FROM member_memories WHERE member = ?", (member,)).rowcount

    # --- 记忆与联想（P2，迁移 25；只存本机） ------------------------------------

    _ITEM_COLS = (
        "member, text, kind, domain, importance, tone, day, source, source_key, hits,"
        " first_at, last_at"
    )

    def add_memory_item(
        self,
        member: str,
        text: str,
        *,
        day: str,
        kind: str = "episode",
        domain: str = "日常",
        importance: int = 1,
        source: str = "night",
        source_key: str | None = None,
        at: str | None = None,
    ) -> dict[str, Any] | None:
        """新增一件记忆；source_key 已存在时不重复写，返回 None。"""
        now = at or self.clock()
        with self._tx():
            cur = self.conn.execute(
                f"INSERT OR IGNORE INTO memory_items ({self._ITEM_COLS})"
                " VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, 1, ?, ?)",
                (member, text, kind, domain, importance, day, source, source_key, now, now),
            )
            if not cur.rowcount:
                return None
            self.conn.execute(
                "INSERT INTO memory_items_fts (rowid, text, member) VALUES (?, ?, ?)",
                (cur.lastrowid, text, member),
            )
        return self.memory_item(cur.lastrowid)

    def add_memory_items_bulk(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """批量写入（导入旧记忆、压力测试用）。

        每行要有 member / text / day，其余同 add_memory_item。
        """
        now = self.clock()
        with self._tx():
            n = 0
            for r in rows:
                at = r.get("at") or now
                cur = self.conn.execute(
                    f"INSERT OR IGNORE INTO memory_items ({self._ITEM_COLS})"
                    " VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, 1, ?, ?)",
                    (
                        r["member"],
                        r["text"],
                        r.get("kind", "episode"),
                        r.get("domain", "日常"),
                        r.get("importance", 1),
                        r["day"],
                        r.get("source", "import"),
                        r.get("source_key"),
                        at,
                        at,
                    ),
                )
                if cur.rowcount:
                    self.conn.execute(
                        "INSERT INTO memory_items_fts (rowid, text, member) VALUES (?, ?, ?)",
                        (cur.lastrowid, r["text"], r["member"]),
                    )
                    n += 1
        return n

    def memory_item(self, item_id: int) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM memory_items WHERE id = ?", (item_id,)).fetchone()
        return dict(row) if row else None

    def memory_item_by_key(self, member: str, source_key: str) -> dict[str, Any] | None:
        row = self._exec(
            "SELECT * FROM memory_items WHERE member = ? AND source_key = ?", (member, source_key)
        ).fetchone()
        return dict(row) if row else None

    def memory_items_by_ids(self, ids: Sequence[int]) -> list[dict[str, Any]]:
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self._exec(f"SELECT * FROM memory_items WHERE id IN ({marks})", tuple(ids))
        return [dict(r) for r in rows.fetchall()]

    def memory_items(
        self,
        member: str,
        *,
        kind: str | None = None,
        min_importance: int = 1,
        since_day: str | None = None,
        day: str | None = None,
        current: bool = True,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """最近的在前。current：只要没被更新掉的件。"""
        sql = "SELECT * FROM memory_items WHERE member = ? AND importance >= ?"
        params: list[Any] = [member, min_importance]
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if since_day is not None:
            sql += " AND day >= ?"
            params.append(since_day)
        if day is not None:
            sql += " AND day = ?"
            params.append(day)
        if current:
            sql += " AND superseded_by IS NULL"
        sql += " ORDER BY last_at DESC, id DESC LIMIT ? OFFSET ?"
        rows = self._exec(sql, (*params, limit, offset)).fetchall()
        return [dict(r) for r in rows]

    def search_memory_items(self, member: str, terms: Sequence[str], limit: int) -> list[int]:
        """全文索引里含任一三字片段的件（按匹配程度），只返回 id。"""
        quoted = [t.replace('"', "") for t in terms if len(t) >= 3]
        quoted = [f'"{t}"' for t in quoted if t]
        if not quoted:
            return []
        rows = self._exec(
            "SELECT rowid FROM memory_items_fts WHERE memory_items_fts MATCH ? AND member = ?"
            " ORDER BY rank LIMIT ?",
            (" OR ".join(quoted), member, limit),
        ).fetchall()
        return [r[0] for r in rows]

    def like_memory_items(self, member: str, words: Sequence[str], limit: int) -> list[int]:
        """含任一两字词的件（三字片段索引查不到两字词时补上），从新到旧最多 limit 件。"""
        words = [w for w in words if w]
        if not words:
            return []
        cond = " OR ".join("text LIKE ?" for _ in words)
        rows = self._exec(
            f"SELECT id FROM memory_items WHERE member = ? AND ({cond}) ORDER BY id DESC LIMIT ?",
            (member, *[f"%{w}%" for w in words], limit),
        ).fetchall()
        return [r[0] for r in rows]

    def count_memory_items(self, member: str | None = None) -> int:
        if member is None:
            return self._exec("SELECT COUNT(*) FROM memory_items").fetchone()[0]
        return self._exec(
            "SELECT COUNT(*) FROM memory_items WHERE member = ?", (member,)
        ).fetchone()[0]

    def memory_item_stats(self, member: str) -> list[dict[str, Any]]:
        """全部经历按领域的件数和合并次数（随身卡的习惯用；一条 SQL，不读全部行）。"""
        rows = self._exec(
            "SELECT domain, COUNT(*) AS items, SUM(hits) AS hits FROM memory_items"
            " WHERE member = ? AND superseded_by IS NULL GROUP BY domain ORDER BY hits DESC",
            (member,),
        ).fetchall()
        return [dict(r) for r in rows]

    def top_memory_items(self, member: str, *, limit: int, min_hits: int = 2) -> list[dict]:
        """全部经历里合并次数最多的件（常做的事、反复提到的喜好）。"""
        rows = self._exec(
            "SELECT * FROM memory_items WHERE member = ? AND superseded_by IS NULL"
            " AND hits >= ? ORDER BY hits DESC, last_at DESC LIMIT ?",
            (member, min_hits, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def bump_memory_item(self, item_id: int, at: str) -> None:
        """相似的事又发生了一次：合并计数，更新最近一次的时间（原文不改）。"""
        self._exec(
            "UPDATE memory_items SET hits = hits + 1, last_at = ? WHERE id = ?", (at, item_id)
        )

    def revise_memory_item(self, item_id: int, field: str, new: Any, reason: str) -> bool:
        """改重要度 / 回忆语气 / 原文（原文只有用户能改），旧值留在 memory_revisions。"""
        if field not in ("importance", "tone", "text", "kind", "domain"):
            raise ValueError(f"不能改 {field}")
        row = self.memory_item(item_id)
        if row is None or str(row[field]) == str(new):
            return False
        now = self.clock()
        with self._tx():
            self.conn.execute(
                "INSERT INTO memory_revisions (item_id, at, field, old, new, reason)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (item_id, now, field, str(row[field]), str(new), reason),
            )
            self.conn.execute(f"UPDATE memory_items SET {field} = ? WHERE id = ?", (new, item_id))
            if field == "text":
                self.conn.execute(
                    "UPDATE memory_items_fts SET text = ? WHERE rowid = ?", (new, item_id)
                )
        return True

    def supersede_memory_item(self, old_id: int, new_id: int) -> None:
        self._exec("UPDATE memory_items SET superseded_by = ? WHERE id = ?", (new_id, old_id))

    def memory_revisions(self, item_id: int) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM memory_revisions WHERE item_id = ? ORDER BY id", (item_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_items_recalled(self, notes: Mapping[int, str]) -> None:
        now = self.clock()
        with self._tx():
            for item_id, note in notes.items():
                self.conn.execute(
                    "UPDATE memory_items SET recalled_at = ?, recall_note = ? WHERE id = ?",
                    (now, note, item_id),
                )

    def memory_items_for_cleanup(self, key_prefix: str, kind: str) -> list[dict[str, Any]]:
        """来源键以 key_prefix 开头、某种类、只出现过一次且从没改过的件（清理旧导入用）。"""
        rows = self._exec(
            "SELECT * FROM memory_items i WHERE substr(source_key, 1, ?) = ? AND kind = ?"
            " AND hits <= 1"
            " AND NOT EXISTS (SELECT 1 FROM memory_revisions r WHERE r.item_id = i.id)",
            (len(key_prefix), key_prefix, kind),
        ).fetchall()
        return [dict(r) for r in rows]

    def memory_items_not_from(self, source: str) -> list[tuple[int, str]]:
        rows = self._exec(
            "SELECT id, text FROM memory_items WHERE source != ?", (source,)
        ).fetchall()
        return [(r["id"], r["text"]) for r in rows]

    def delete_memory_item(self, item_id: int) -> bool:
        with self._tx():
            n = self.conn.execute("DELETE FROM memory_items WHERE id = ?", (item_id,)).rowcount
            self.conn.execute("DELETE FROM memory_items_fts WHERE rowid = ?", (item_id,))
            self.conn.execute("DELETE FROM memory_revisions WHERE item_id = ?", (item_id,))
            self.conn.execute("DELETE FROM memory_links WHERE a = ? OR b = ?", (item_id, item_id))
        return n > 0

    # 挂着的事

    def memory_hanging(self, member: str, status: str = "open") -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM memory_hanging WHERE member = ? AND status = ? ORDER BY since, id",
            (member, status),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_memory_hanging(self, member: str, text: str, domain: str, since: str) -> int:
        cur = self._exec(
            "INSERT INTO memory_hanging (member, text, domain, status, since, updated_at)"
            " VALUES (?, ?, ?, 'open', ?, ?)",
            (member, text, domain, since, self.clock()),
        )
        return cur.lastrowid

    def close_memory_hanging(self, hanging_id: int, status: str) -> bool:
        return (
            self._exec(
                "UPDATE memory_hanging SET status = ?, updated_at = ? WHERE id = ?",
                (status, self.clock(), hanging_id),
            ).rowcount
            > 0
        )

    # 联想网

    def memory_links_of(self, member: str, ids: Sequence[int]) -> list[tuple[int, int, float]]:
        """这些件的全部连接 (a, b, 强度)。"""
        if not ids:
            return []
        marks = ",".join("?" * len(ids))
        rows = self._exec(
            f"SELECT a, b, weight FROM memory_links WHERE member = ? AND (a IN ({marks})"
            f" OR b IN ({marks}))",
            (member, *ids, *ids),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def set_memory_links(self, member: str, links: Mapping[tuple[int, int], float]) -> None:
        now = self.clock()
        with self._tx():
            for (a, b), w in links.items():
                self.conn.execute(
                    "INSERT INTO memory_links (member, a, b, weight, updated_at)"
                    " VALUES (?, ?, ?, ?, ?) ON CONFLICT(member, a, b) DO UPDATE SET"
                    " weight = excluded.weight, updated_at = excluded.updated_at",
                    (member, a, b, w, now),
                )

    def decay_memory_links(self, member: str, factor: float, min_weight: float) -> int:
        """每条连接乘以 factor，低于 min_weight 的删掉。返回删掉的条数。"""
        with self._tx():
            self.conn.execute(
                "UPDATE memory_links SET weight = weight * ? WHERE member = ?", (factor, member)
            )
            return self.conn.execute(
                "DELETE FROM memory_links WHERE member = ? AND weight < ?", (member, min_weight)
            ).rowcount

    def trim_memory_links(self, member: str, item_id: int, keep: int) -> int:
        """一件记忆最多留 keep 条最强的连接。"""
        rows = self._exec(
            "SELECT a, b FROM memory_links WHERE member = ? AND (a = ? OR b = ?)"
            " ORDER BY weight DESC LIMIT -1 OFFSET ?",
            (member, item_id, item_id, keep),
        ).fetchall()
        with self._tx():
            for a, b in rows:
                self.conn.execute(
                    "DELETE FROM memory_links WHERE member = ? AND a = ? AND b = ?",
                    (member, a, b),
                )
        return len(rows)

    def count_memory_links(self, member: str) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM memory_links WHERE member = ?", (member,)
        ).fetchone()[0]

    # 全量经历的习惯摘要

    def memory_digest(self, member: str) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM memory_digest WHERE member = ?", (member,)).fetchone()
        if row is None:
            return None
        return dict(row) | {"stats": json.loads(row["stats"])}

    def set_memory_digest(self, member: str, text: str, stats: Mapping[str, Any]) -> None:
        self._exec(
            "INSERT INTO memory_digest (member, text, stats, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(member) DO UPDATE SET text = excluded.text, stats = excluded.stats,"
            " updated_at = excluded.updated_at",
            (member, text, _json(dict(stats)), self.clock()),
        )

    # 翻日记

    def add_diary_read(self, member: str, at: str, query: str, found: int) -> None:
        self._exec(
            "INSERT INTO memory_diary_reads (member, at, query, found) VALUES (?, ?, ?, ?)",
            (member, at, query, found),
        )

    def diary_reads_between(self, member: str, start: str, end: str) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM memory_diary_reads WHERE member = ? AND at >= ? AND at < ?",
            (member, start, end),
        ).fetchone()[0]

    def search_diaries(self, member: str, words: Sequence[str], limit: int) -> list[dict]:
        """成员自己的日记（member_memories 的 day 层）里含任一关键词的，新的在前。"""
        words = [w for w in words if w]
        if not words:
            return []
        cond = " OR ".join("text LIKE ?" for _ in words)
        rows = self._exec(
            f"SELECT * FROM member_memories WHERE member = ? AND layer = 'day' AND ({cond})"
            " ORDER BY period DESC LIMIT ?",
            (member, *[f"%{w}%" for w in words], limit),
        ).fetchall()
        return [_memory_row(r) for r in rows]

    # 训练数据（不过期）

    def add_memory_train(self, member: str, kind: str, data: Mapping[str, Any]) -> None:
        self._exec(
            "INSERT INTO memory_train (at, member, kind, data) VALUES (?, ?, ?, ?)",
            (self.clock(), member, kind, _json(dict(data))),
        )

    def memory_train(self, member: str | None = None, limit: int = 1000) -> list[dict]:
        sql, params = "SELECT * FROM memory_train", []
        if member is not None:
            sql += " WHERE member = ?"
            params.append(member)
        rows = self._exec(sql + " ORDER BY id DESC LIMIT ?", (*params, limit)).fetchall()
        return [dict(r) | {"data": json.loads(r["data"])} for r in rows]

    def clear_memory_p2(self, member: str | None = None) -> int:
        """一键清空记忆件、改动记录、挂着的事、联想网、习惯摘要、翻日记记录和训练数据。"""
        tables = (
            "memory_items",
            "memory_hanging",
            "memory_links",
            "memory_digest",
            "memory_diary_reads",
            "memory_train",
        )
        n = 0
        with self._tx():
            if member is None:
                self.conn.execute("DELETE FROM memory_revisions")
                self.conn.execute("DELETE FROM memory_items_fts")
                for t in tables:
                    n += self.conn.execute(f"DELETE FROM {t}").rowcount
            else:
                self.conn.execute(
                    "DELETE FROM memory_revisions WHERE item_id IN"
                    " (SELECT id FROM memory_items WHERE member = ?)",
                    (member,),
                )
                self.conn.execute("DELETE FROM memory_items_fts WHERE member = ?", (member,))
                for t in tables:
                    n += self.conn.execute(f"DELETE FROM {t} WHERE member = ?", (member,)).rowcount
        return n

    def work_records_between(
        self, start: datetime, end: datetime, workflows: Sequence[str]
    ) -> list[dict[str, Any]]:
        """[start, end) 内这些工作模式里每个模型参加过的场次：
        模型、会话、模式、题目、做了哪些步骤、是否匿名、揭晓时间。生活只读自己的（由调用方挑）。"""
        marks = ",".join("?" * len(workflows))
        rows = self._exec(
            "SELECT c.model_id, s.id AS session_id, s.workflow, s.question, s.anonymous,"
            " s.revealed_at, GROUP_CONCAT(DISTINCT c.step) AS steps, MAX(c.created_at) AS at"
            " FROM calls c JOIN sessions s ON s.id = c.session_id"
            f" WHERE s.workflow IN ({marks}) AND c.role IN ('member', 'coordinator')"
            " AND c.model_id IS NOT NULL AND c.created_at >= ? AND c.created_at < ?"
            " GROUP BY c.model_id, s.id ORDER BY at",
            (*workflows, iso(start), iso(end)),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_life_event(
        self,
        at: str,
        kind: str,
        members: Sequence[str],
        *,
        place: str = "",
        topic: str = "",
        room: str | None = None,
        written: bool = False,
        offline: bool = False,
    ) -> dict[str, Any]:
        cur = self._exec(
            "INSERT INTO life_events (at, kind, place, members, topic, room, written, offline,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                at,
                kind,
                place,
                json.dumps(list(members)),
                topic,
                room,
                int(written),
                int(offline),
                self.clock(),
            ),
        )
        row = self._exec("SELECT * FROM life_events WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _life_row(row)

    def life_events_between(self, start: str, end: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM life_events WHERE at >= ? AND at < ? ORDER BY at, id", (start, end)
        ).fetchall()
        return [_life_row(r) for r in rows]

    # --- 收得住（docs/CHAT.md G9） ------------------------------------------------

    def life_messages_between(self, start: str, end: str) -> list[dict[str, Any]]:
        """[start, end) 内成员之间的生活消息（meta.life，含分享），按时间先后。"""
        rows = self._exec(
            "SELECT * FROM chat_messages WHERE json_extract(meta, '$.life') = 1"
            " AND created_at >= ? AND created_at < ? ORDER BY created_at, id",
            (start, end),
        ).fetchall()
        return [_chat_row(r) for r in rows]

    def count_life_messages(self, start: str, end: str) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM chat_messages WHERE json_extract(meta, '$.life') = 1"
            " AND created_at >= ? AND created_at < ?",
            (start, end),
        ).fetchone()[0]

    def recent_life_messages(self, room: str, limit: int) -> list[dict[str, Any]]:
        """某个会话里最近 limit 条生活消息（按时间先后）。"""
        rows = self._exec(
            "SELECT * FROM chat_messages WHERE room = ? AND json_extract(meta, '$.life') = 1"
            " ORDER BY id DESC LIMIT ?",
            (room, limit),
        ).fetchall()
        return [_chat_row(r) for r in reversed(rows)]

    def member_message_counts(self, start: str, end: str) -> list[dict[str, Any]]:
        """[start, end) 内每位作者的消息数（life：成员之间的；其余是回应用户的）与字数。"""
        rows = self._exec(
            "SELECT author, COALESCE(json_extract(meta, '$.life'), 0) = 1 AS life,"
            " COUNT(*) AS n, COALESCE(SUM(LENGTH(text)), 0) AS chars FROM chat_messages"
            " WHERE created_at >= ? AND created_at < ? GROUP BY author, life",
            (start, end),
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_call_errors(
        self, workflow: str, steps: Sequence[str], start: datetime, end: datetime, limit: int
    ) -> list[bool]:
        """[start, end) 内某类隐藏会话里、指定步骤最近 limit 次调用是否出错（新的在前）。"""
        marks = ", ".join("?" for _ in steps)
        rows = self._exec(
            "SELECT c.error IS NOT NULL AS failed FROM calls c JOIN sessions s"
            f" ON s.id = c.session_id WHERE s.workflow = ? AND c.step IN ({marks})"
            " AND c.created_at >= ? AND c.created_at < ? ORDER BY c.id DESC LIMIT ?",
            (workflow, *steps, iso(start), iso(end), limit),
        ).fetchall()
        return [bool(r["failed"]) for r in rows]

    def calls_by_step(self, workflow: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """[start, end) 内某类隐藏会话里每个步骤的调用次数、出错次数和花费。"""
        rows = self._exec(
            "SELECT c.step AS step, COUNT(*) AS calls,"
            " SUM(CASE WHEN c.error IS NULL THEN 0 ELSE 1 END) AS failed,"
            " COALESCE(SUM(c.cost_usd), 0) AS cost_usd FROM calls c JOIN sessions s"
            " ON s.id = c.session_id WHERE s.workflow = ? AND c.created_at >= ?"
            " AND c.created_at < ? GROUP BY c.step ORDER BY c.step",
            (workflow, iso(start), iso(end)),
        ).fetchall()
        return [dict(r) for r in rows]

    def add_guard_log(
        self, kind: str, detail: str = "", *, member: str | None = None, at: str | None = None
    ) -> None:
        self._exec(
            "INSERT INTO life_guard_log (at, kind, member, detail) VALUES (?, ?, ?, ?)",
            (at or self.clock(), kind, member, detail),
        )

    def guard_log_between(self, start: str, end: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM life_guard_log WHERE at >= ? AND at < ? ORDER BY at, id", (start, end)
        ).fetchall()
        return [dict(r) for r in rows]

    def save_life_report(self, week: str, report: dict[str, Any]) -> None:
        self._exec(
            "INSERT INTO life_reports (week, created_at, report) VALUES (?, ?, ?)"
            " ON CONFLICT(week) DO UPDATE SET created_at = excluded.created_at,"
            " report = excluded.report",
            (week, self.clock(), json.dumps(report, ensure_ascii=False)),
        )

    def life_reports(self, limit: int = 12) -> list[dict[str, Any]]:
        """最近的每周自检报告（新的在前）。"""
        rows = self._exec(
            "SELECT * FROM life_reports ORDER BY week DESC LIMIT ?", (limit,)
        ).fetchall()
        return [
            {"week": r["week"], "created_at": r["created_at"], **json.loads(r["report"])}
            for r in rows
        ]

    # --- 成员引擎（docs/MEMBER_ENGINE.md） --------------------------------------------

    def member_profile(self, member: str, version: int | None = None) -> dict[str, Any] | None:
        """某位成员的档案：最新版（version 为空）或指定版本。"""
        if version is None:
            row = self._exec(
                "SELECT * FROM member_profiles WHERE member = ? ORDER BY version DESC LIMIT 1",
                (member,),
            ).fetchone()
        else:
            row = self._exec(
                "SELECT * FROM member_profiles WHERE member = ? AND version = ?", (member, version)
            ).fetchone()
        if row is None:
            return None
        return {"version": row["version"], "data": json.loads(row["data"])}

    def member_profile_history(self, member: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT version, author, reason, changes, created_at FROM member_profiles"
            " WHERE member = ? ORDER BY version DESC",
            (member,),
        ).fetchall()
        return [{**dict(r), "changes": json.loads(r["changes"])} for r in rows]

    def add_member_profile(
        self,
        member: str,
        version: int,
        data: dict[str, Any],
        *,
        author: str,
        reason: str,
        changes: list[dict[str, Any]],
    ) -> None:
        self._exec(
            "INSERT INTO member_profiles (member, version, data, author, reason, changes,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                member,
                version,
                json.dumps(data, ensure_ascii=False, sort_keys=True),
                author,
                reason,
                json.dumps(changes, ensure_ascii=False),
                self.clock(),
            ),
        )

    def clear_member_profiles(self, member: str) -> int:
        return self._exec("DELETE FROM member_profiles WHERE member = ?", (member,)).rowcount

    def add_member_event(
        self, at: str, kind: str, member: str | None, data: dict[str, Any]
    ) -> None:
        self._exec(
            "INSERT INTO member_events (at, kind, member, data) VALUES (?, ?, ?, ?)",
            (at, kind, member, json.dumps(data, ensure_ascii=False)),
        )

    def member_events_between(self, start: str, end: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM member_events WHERE at >= ? AND at < ? ORDER BY at, id", (start, end)
        ).fetchall()
        return [{**dict(r), "data": json.loads(r["data"])} for r in rows]

    def clear_member_events(self) -> int:
        return self._exec("DELETE FROM member_events").rowcount

    def count_member_events(self) -> int:
        return self._exec("SELECT COUNT(*) FROM member_events").fetchone()[0]

    def prune_member_events(self, before: str) -> int:
        return self._exec("DELETE FROM member_events WHERE at < ?", (before,)).rowcount

    def trim_chat_prompts(self, before: str, keep_chars: int) -> int:
        """聊天 / 生活隐藏会话（workflow = 'chat'）里 before 以前的调用：整段提示词换成一条说明
        加最后一段的开头。回复、花费、token 都不动。返回精简了几条。"""
        rows = self._exec(
            "SELECT c.id, c.input FROM calls c JOIN sessions s ON s.id = c.session_id"
            " WHERE s.workflow = 'chat' AND c.created_at < ? AND LENGTH(c.input) > ?",
            (before, keep_chars + 200),
        ).fetchall()
        trimmed = 0
        with self._tx():
            for row in rows:
                messages = json.loads(row["input"])
                if not messages or messages[0].get("role") == "trimmed":
                    continue
                size = sum(len(str(m.get("content", ""))) for m in messages)
                last = messages[-1]
                note = f"（提示词已精简：原来 {len(messages)} 段、{size} 字）"
                head = str(last.get("content", ""))[:keep_chars]
                short = [
                    {"role": "trimmed", "content": note},
                    {"role": last.get("role", "user"), "content": head},
                ]
                self.conn.execute(
                    "UPDATE calls SET input = ? WHERE id = ?", (_json(short), row["id"])
                )
                trimmed += 1
        return trimmed

    # --- 上网看到的内容（docs/CHAT.md G7） ----------------------------------------------

    def add_web_item(
        self,
        feed: str,
        url: str,
        title: str,
        *,
        summary: str = "",
        published: str | None = None,
        topics: Sequence[str] = (),
        unverified: bool = True,
    ) -> bool:
        """存一条；同一网址已有时不改，返回 False。"""
        cur = self._exec(
            "INSERT OR IGNORE INTO web_items (feed, url, title, summary, published, topics,"
            " unverified, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                feed,
                url,
                title,
                summary,
                published,
                json.dumps(list(topics), ensure_ascii=False),
                int(unverified),
                self.clock(),
            ),
        )
        return cur.rowcount > 0

    def web_items(self, *, unshared: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        """新的在前。"""
        sql = "SELECT * FROM web_items"
        if unshared:
            sql += " WHERE shared_by IS NULL"
        sql += " ORDER BY fetched_at DESC, id DESC LIMIT ?"
        return [_web_row(r) for r in self._exec(sql, (limit,)).fetchall()]

    def web_shared_by(self, member: str, since: str) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM web_items WHERE shared_by = ? AND shared_at >= ?",
            (member, since),
        ).fetchone()[0]

    def web_item_by_url(self, url: str) -> dict[str, Any] | None:
        row = self._exec("SELECT * FROM web_items WHERE url = ?", (url,)).fetchone()
        return _web_row(row) if row else None

    def web_items_shared_by(self, member: str, since: str) -> list[dict[str, Any]]:
        """这位成员 since 之后分享过的条目（新的在前）。"""
        rows = self._exec(
            "SELECT * FROM web_items WHERE shared_by = ? AND shared_at >= ?"
            " ORDER BY shared_at DESC, id DESC",
            (member, since),
        ).fetchall()
        return [_web_row(r) for r in rows]

    def mark_web_shared(self, item_id: int, member: str) -> None:
        self._exec(
            "UPDATE web_items SET shared_by = ?, shared_at = ? WHERE id = ?",
            (member, self.clock(), item_id),
        )

    def prune_web_items(self, before: str) -> int:
        """删掉 before 之前抓到、没分享过的条目。"""
        cur = self._exec(
            "DELETE FROM web_items WHERE shared_by IS NULL AND fetched_at < ?", (before,)
        )
        return cur.rowcount
