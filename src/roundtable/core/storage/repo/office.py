"""办公室模式的绩效与贡献统计。

从 repository.py 拆出（代码精简）。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from roundtable.core.storage.records import ChannelUsage, iso


class OfficeRepo:
    """办公室模式的绩效与贡献统计。"""

    # --- 办公室模式：绩效 ---------------------------------------------------------

    def setting(self, key: str) -> str | None:
        row = self._exec("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = excluded.updated_at",
            (key, value, self.clock()),
        )

    def office_sessions(self, since: str | None = None, limit: int = 500) -> list[str]:
        """办公室模式的会话 id（新的在前）：创建于 since 之后；匿名未揭晓的不算（不暴露身份）。"""
        visible = "anonymous = 0 OR revealed_at IS NOT NULL"
        sql = f"SELECT id FROM sessions WHERE workflow = 'office' AND ({visible})"
        params: list[Any] = []
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return [r["id"] for r in self._exec(sql, params).fetchall()]

    def member_call_totals(self, session_id: str, table_no: int) -> dict[str, tuple[float, float]]:
        """某张桌子上每位组员（代号）的调用花费与用时合计（秒）。"""
        rows = self._exec(
            "SELECT code, COALESCE(SUM(cost_usd), 0) AS cost, COALESCE(SUM(latency_s), 0) AS secs"
            " FROM calls WHERE session_id = ? AND table_no = ? AND role = 'member'"
            " AND code IS NOT NULL GROUP BY code",
            (session_id, table_no),
        ).fetchall()
        return {r["code"]: (float(r["cost"]), float(r["secs"])) for r in rows}

    def usage_rows(
        self,
        start: datetime | None,
        end: datetime | None,
        meeting_ids: Sequence[str] = (),
    ) -> list[dict[str, Any]]:
        """[start, end) 内的调用按（UTC 日期、会话类型、步骤、模型、渠道）汇总（余额与用量面板）。

        start / end 为空表示不限。hidden 为真表示匿名开启且未揭晓：调用方不得按模型展示这些行。
        meeting 为真表示调用属于 meeting_ids 里的会话（革新例会）。
        """
        marks = ", ".join("?" for _ in meeting_ids) or "NULL"
        where, params = [], []
        if start is not None:
            where.append("c.created_at >= ?")
            params.append(iso(start))
        if end is not None:
            where.append("c.created_at < ?")
            params.append(iso(end))
        rows = self._exec(
            "SELECT substr(c.created_at, 1, 10) AS day, s.workflow, c.step, c.model_id,"
            " c.channel, (s.anonymous = 1 AND s.revealed_at IS NULL) AS hidden,"
            f" (c.session_id IN ({marks})) AS meeting, COUNT(*) AS calls,"
            " SUM(c.error IS NOT NULL) AS failed, COALESCE(SUM(c.input_tokens), 0) AS tin,"
            " COALESCE(SUM(c.output_tokens), 0) AS tout,"
            " COALESCE(SUM(c.cached_tokens), 0) AS tcached,"
            " COALESCE(SUM(c.reasoning_tokens), 0) AS treason,"
            " COALESCE(SUM(c.cost_usd), 0) AS cost"
            " FROM calls c JOIN sessions s ON s.id = c.session_id"
            + (f" WHERE {' AND '.join(where)}" if where else "")
            + " GROUP BY day, s.workflow, c.step, c.model_id, c.channel, hidden, meeting",
            (*meeting_ids, *params),
        ).fetchall()
        return [
            {
                "day": r["day"],
                "workflow": r["workflow"],
                "step": r["step"],
                "model_id": r["model_id"],
                "channel": r["channel"],
                "hidden": bool(r["hidden"]),
                "meeting": bool(r["meeting"]),
                "calls": r["calls"],
                "failed": r["failed"] or 0,
                "input_tokens": r["tin"],
                "output_tokens": r["tout"],
                "cached_tokens": r["tcached"],
                "reasoning_tokens": r["treason"],
                "cost_usd": r["cost"],
            }
            for r in rows
        ]

    def spent_by_channel(self) -> dict[str, ChannelUsage]:
        usage: dict[str, ChannelUsage] = {}
        for r in self._exec(
            "SELECT channel, COUNT(*) AS calls, SUM(input_tokens) AS tin,"
            " SUM(output_tokens) AS tout, SUM(cost_usd) AS cost"
            " FROM calls WHERE channel IS NOT NULL GROUP BY channel"
        ).fetchall():
            usage[r["channel"]] = ChannelUsage(r["calls"], 0, r["tin"], r["tout"], r["cost"])
        for r in self._exec(
            "SELECT channel, COUNT(*) AS n FROM call_attempts WHERE ok = 0 GROUP BY channel"
        ).fetchall():
            usage.setdefault(r["channel"], ChannelUsage()).failed_attempts = r["n"]
        return usage

    # --- 贡献 ------------------------------------------------------------------

    def replace_contributions(
        self,
        session_id: str,
        table_no: int,
        rows: Sequence[tuple[str, str, str, int]],
    ) -> None:
        """整桌重写（可重复调用）：rows 为 (代号, 模型 id, 类别, 数量)，数量为 0 的不存。"""
        self._require_session(session_id)
        now = self.clock()
        with self._tx():
            self.conn.execute(
                "DELETE FROM contributions WHERE session_id = ? AND table_no = ?",
                (session_id, table_no),
            )
            self.conn.executemany(
                "INSERT INTO contributions (session_id, table_no, code, model_id, kind, amount,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (session_id, table_no, code, model, kind, amount, now)
                    for code, model, kind, amount in rows
                    if amount
                ],
            )

    def contributions(self, session_id: str) -> list[dict[str, Any]]:
        """内部使用（含模型 id）。"""
        rows = self._exec(
            "SELECT table_no, code, model_id, kind, amount FROM contributions"
            " WHERE session_id = ? ORDER BY table_no, code, kind",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def contribution_history(self) -> list[dict[str, Any]]:
        """跨会话按模型汇总。只统计身份已经公开的会话（匿名关闭，或匿名但已揭晓），
        避免从统计里反推出未揭晓会话中各代号的身份。"""
        rows = self._exec(
            "SELECT c.model_id, c.kind, SUM(c.amount) AS amount,"
            " COUNT(DISTINCT c.session_id) AS sessions"
            " FROM contributions c JOIN sessions s ON s.id = c.session_id"
            " WHERE s.anonymous = 0 OR s.revealed_at IS NOT NULL"
            " GROUP BY c.model_id, c.kind ORDER BY c.model_id, c.kind",
        ).fetchall()
        return [dict(r) for r in rows]
