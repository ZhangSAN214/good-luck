"""附件、工具与生成的文件、媒体任务、用量。

从 repository.py 拆出（代码精简）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from roundtable.core.storage.records import NotFound, _attachment, _json, _now, iso


class FilesRepo:
    """附件、工具与生成的文件、媒体任务、用量。"""

    # --- 附件 ------------------------------------------------------------------

    def add_attachment(self, **fields: Any) -> str:
        """登记一个上传的文件（尚未关联会话）。返回附件 id。"""
        attachment_id = fields.pop("id", None) or uuid.uuid4().hex
        warnings = list(fields.pop("warnings", ()))
        with self._tx():
            self.conn.execute(
                "INSERT INTO attachments (id, name, kind, mime, ext, size, sha256, storage_key,"
                " pages, text, text_source, status, error, warnings, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    attachment_id,
                    fields["name"],
                    fields["kind"],
                    fields["mime"],
                    fields["ext"],
                    fields["size"],
                    fields["sha256"],
                    fields["storage_key"],
                    fields.get("pages"),
                    fields.get("text"),
                    fields.get("text_source"),
                    fields["status"],
                    fields.get("error"),
                    _json(warnings),
                    _now(),
                ),
            )
        return attachment_id

    def attachment(self, attachment_id: str) -> dict[str, Any]:
        row = self._exec("SELECT * FROM attachments WHERE id = ?", (attachment_id,)).fetchone()
        if row is None:
            raise NotFound(f"附件不存在：{attachment_id}")
        return _attachment(row)

    def attach_to_session(
        self,
        session_id: str,
        attachment_ids: Sequence[str],
        style_refs: Sequence[str] | None = None,
    ) -> None:
        """按给定顺序把上传的文件关联到会话；已属于其他会话的文件不能再用。

        style_refs：作为风格参考的附件 id（只对图片有意义）；None 表示全部图片都是风格参考。
        """
        self._require_session(session_id)
        with self._tx():
            for position, attachment_id in enumerate(attachment_ids):
                flag = 1 if style_refs is None or attachment_id in style_refs else 0
                cur = self.conn.execute(
                    "UPDATE attachments SET session_id = ?, position = ?, style_ref = ?"
                    " WHERE id = ? AND session_id IS NULL",
                    (session_id, position, flag, attachment_id),
                )
                if cur.rowcount != 1:
                    raise NotFound(f"附件不存在或已被使用：{attachment_id}")

    def session_attachments(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM attachments WHERE session_id = ? ORDER BY position", (session_id,)
        ).fetchall()
        return [_attachment(r) for r in rows]

    def update_attachment(
        self,
        attachment_id: str,
        *,
        status: str,
        text: str | None = None,
        text_source: str | None = None,
        error: str | None = None,
    ) -> None:
        self._exec(
            "UPDATE attachments SET status = ?, text = COALESCE(?, text),"
            " text_source = COALESCE(?, text_source), error = ? WHERE id = ?",
            (status, text, text_source, error, attachment_id),
        )

    # --- 工具与生成的文件 ----------------------------------------------------------

    def record_tool_call(
        self,
        session_id: str,
        *,
        table_no: int,
        step: str,
        code: str | None,
        round_no: int,
        tool: str,
        input: Mapping[str, Any],
        output: str | None,
        status: str,
        duration_s: float | None = None,
        cost_usd: float = 0.0,
        call_id: int | None = None,
    ) -> int:
        with self._tx():
            cur = self.conn.execute(
                "INSERT INTO tool_calls (session_id, table_no, step, code, round, tool, input,"
                " output, status, duration_s, cost_usd, call_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    table_no,
                    step,
                    code,
                    round_no,
                    tool,
                    _json(dict(input)),
                    output,
                    status,
                    duration_s,
                    cost_usd,
                    call_id,
                    _now(),
                ),
            )
        return int(cur.lastrowid)

    def tool_calls(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["input"] = json.loads(d["input"])
            out.append(d)
        return out

    def add_file(self, session_id: str, **fields: Any) -> str:
        file_id = uuid.uuid4().hex
        with self._tx():
            self.conn.execute(
                "INSERT INTO files (id, session_id, table_no, step, code, tool_call_id, path, kind,"
                " mime, size, sha256, storage_key, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    file_id,
                    session_id,
                    fields["table_no"],
                    fields["step"],
                    fields.get("code"),
                    fields.get("tool_call_id"),
                    fields["path"],
                    fields["kind"],
                    fields["mime"],
                    fields["size"],
                    fields["sha256"],
                    fields["storage_key"],
                    _now(),
                ),
            )
        return file_id

    def link_files(self, file_ids: Sequence[str], tool_call_id: int) -> None:
        """把文件登记到生成它的工具调用名下。"""
        with self._tx():
            self.conn.executemany(
                "UPDATE files SET tool_call_id = ? WHERE id = ?",
                [(tool_call_id, f) for f in file_ids],
            )

    def files(self, session_id: str) -> list[dict[str, Any]]:
        """本场生成的全部文件（按生成顺序，含同一路径的旧版本）。"""
        rows = self._exec(
            "SELECT * FROM files WHERE session_id = ? ORDER BY created_at, rowid", (session_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def file(self, session_id: str, file_id: str) -> dict[str, Any]:
        row = self._exec(
            "SELECT * FROM files WHERE session_id = ? AND id = ?", (session_id, file_id)
        ).fetchone()
        if row is None:
            raise NotFound(f"文件不存在：{file_id}")
        return dict(row)

    def session_file_bytes(self, session_id: str) -> int:
        return self._exec(
            "SELECT COALESCE(SUM(size), 0) FROM files WHERE session_id = ?", (session_id,)
        ).fetchone()[0]

    # --- 媒体任务 ----------------------------------------------------------------

    def create_media_job(self, session_id: str, **f: Any) -> int:
        now = _now()
        cur = self._exec(
            "INSERT INTO media_jobs (session_id, table_no, step, code, subtask, round, attempt,"
            " kind, model_id, state, prompt, params, reference_count, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                f["table_no"],
                f["step"],
                f.get("code"),
                f.get("subtask"),
                f["round"],
                f.get("attempt", 1),
                f["kind"],
                f["model_id"],
                "submitted",
                f["prompt"],
                _json(f.get("params", {})),
                f.get("reference_count", 0),
                now,
                now,
            ),
        )
        return cur.lastrowid

    def update_media_job(self, job_id: int, **fields: Any) -> None:
        allowed = {
            "channel",
            "route_model",
            "external_id",
            "polling_url",
            "state",
            "cost_usd",
            "error",
            "file_id",
            "submitted_at",
            "params",
            "reference_count",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"不能更新的字段：{sorted(bad)}")
        if "params" in fields:
            fields["params"] = _json(fields["params"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._exec(
            f"UPDATE media_jobs SET {sets}, updated_at = ? WHERE id = ?",
            (*fields.values(), _now(), job_id),
        )

    def media_jobs(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM media_jobs WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"])
            out.append(d)
        return out

    # --- 用量 ------------------------------------------------------------------

    def total_spent(self) -> float:
        return self._exec("SELECT COALESCE(SUM(cost_usd), 0) FROM calls").fetchone()[0]

    def spent_between(self, start: datetime, end: datetime) -> float:
        """[start, end) 内的花费（按调用记录时间）。"""
        return self._exec(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE created_at >= ? AND created_at < ?",
            (iso(start), iso(end)),
        ).fetchone()[0]

    def spent_by_channel_between(self, start: datetime, end: datetime) -> dict[str, float]:
        """[start, end) 内按渠道的花费（按调用记录时间）。"""
        rows = self._exec(
            "SELECT channel, COALESCE(SUM(cost_usd), 0) AS cost FROM calls"
            " WHERE channel IS NOT NULL AND created_at >= ? AND created_at < ? GROUP BY channel",
            (iso(start), iso(end)),
        ).fetchall()
        return {r["channel"]: r["cost"] for r in rows}

    def spent_in_sessions(
        self, session_ids: Sequence[str], start: datetime, end: datetime
    ) -> float:
        """[start, end) 内指定几场会话的调用花费（如革新例会的每月上限）。"""
        if not session_ids:
            return 0.0
        marks = ", ".join("?" for _ in session_ids)
        return self._exec(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls"
            f" WHERE session_id IN ({marks}) AND created_at >= ? AND created_at < ?",
            (*session_ids, iso(start), iso(end)),
        ).fetchone()[0]

    def session_cost(self, session_id: str) -> float:
        return self._exec(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE session_id = ?", (session_id,)
        ).fetchone()[0]

    def table_cost(self, session_id: str, table_no: int, *, include_media: bool = True) -> float:
        sql = "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE session_id = ? AND table_no = ?"
        if not include_media:
            sql += " AND role != 'media'"
        return self._exec(sql, (session_id, table_no)).fetchone()[0]

    def call_samples(self, sessions: int = 30) -> list[dict[str, Any]]:
        """最近若干场讨论中各步骤的调用（成员与统筹，不含规划员），供花费预估用历史校准。"""
        rows = self._exec(
            "SELECT session_id, table_no, step, code, role, model_id, input_tokens,"
            " output_tokens, error FROM calls WHERE table_no IS NOT NULL"
            " AND role IN ('member', 'coordinator') AND session_id IN"
            " (SELECT id FROM sessions ORDER BY created_at DESC LIMIT ?)",
            (sessions,),
        ).fetchall()
        return [dict(r) for r in rows]
