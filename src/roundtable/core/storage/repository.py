"""数据访问。其他模块不直接写 SQL。

对外展示一律走 session_view()：揭晓前不返回模型 id、渠道、切换记录，
并可传入身份遮蔽器处理模型输出（防止输出里的自报身份泄露给用户）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from roundtable.core.prompts import RenderedPrompt
from roundtable.core.providers import (
    AllChannelsFailed,
    Completion,
    Message,
    NoChannelAvailable,
    ProviderError,
)
from roundtable.core.routing import PlannerResult, RoutingRecord

Status = Literal[
    "created", "awaiting_confirmation", "running", "paused", "completed", "failed", "stopped"
]
STATUSES = frozenset(Status.__args__)  # type: ignore[attr-defined]
Role = Literal["planner", "member", "coordinator", "preprocess"]  # preprocess：附件预处理
HIDDEN_ERROR = "出错（详情揭晓后可见）"


class NotFound(LookupError):
    pass


def iso(moment: datetime) -> str:
    """库中时间戳的统一格式（UTC，毫秒），可按字符串比较先后。"""
    if moment.tzinfo is None:
        raise ValueError("时间必须带时区")
    return moment.astimezone(UTC).isoformat(timespec="milliseconds")


def _now() -> str:
    return iso(datetime.now(UTC))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# --- 视图（对外展示用） --------------------------------------------------------


@dataclass(frozen=True)
class SeatView:
    table_no: int
    role: str
    code: str | None
    model_id: str | None  # 揭晓前为 None


@dataclass(frozen=True)
class OutputView:
    table_no: int
    step: str
    kind: str
    code: str | None
    content: str


@dataclass(frozen=True)
class CallView:
    id: int
    table_no: int | None
    step: str
    role: str
    code: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float | None
    failed: bool
    error: str | None
    # 以下揭晓后才有
    model_id: str | None = None
    channel: str | None = None
    attempts: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CheckpointView:
    id: int
    kind: str
    card: dict[str, Any]
    status: str
    response: str | None
    note: str | None


@dataclass(frozen=True)
class SessionView:
    id: str
    created_at: str
    question: str
    status: str
    tier: str  # 成员档位：budget / flagship / custom（旧会话为 auto / preset / manual）
    workflow: str  # discussion / collab
    anonymous: bool
    # 是否显示身份：匿名关闭时始终为真；匿名开启时用户点"揭晓身份"后为真
    revealed: bool
    routing: dict[str, Any] | None
    seats: tuple[SeatView, ...]
    outputs: tuple[OutputView, ...]
    calls: tuple[CallView, ...]
    checkpoints: tuple[CheckpointView, ...]
    cost_usd: float
    error: str | None = None


@dataclass(frozen=True)
class SessionSummary:
    id: str
    created_at: str
    question: str
    status: str
    difficulty: str | None
    plan: str | None
    cost_usd: float
    revealed: bool
    anonymous: bool
    workflow: str


@dataclass
class ChannelUsage:
    calls: int = 0
    failed_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


# 揭晓前从路由记录中去掉的字段（阵容与规划员模型都是身份信息）
_ROUTING_PRIVATE = (
    "members",
    "coordinator",
    "absent",
    "planner_model",
    "planner_error",
    "extra",
)


@dataclass
class Repository:
    conn: sqlite3.Connection
    clock: Callable[[], str] = _now
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # --- 内部 ------------------------------------------------------------------

    def _exec(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, params)

    def _tx(self):
        repo = self

        class _Tx:
            def __enter__(self):
                repo._lock.acquire()
                repo.conn.execute("BEGIN")

            def __exit__(self, exc_type, exc, tb):
                try:
                    repo.conn.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    repo._lock.release()

        return _Tx()

    def _require_session(self, session_id: str) -> sqlite3.Row:
        row = self._exec("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            raise NotFound(f"会话 {session_id} 不存在")
        return row

    def _touch(self, session_id: str) -> None:
        self._exec("UPDATE sessions SET updated_at = ? WHERE id = ?", (self.clock(), session_id))

    # --- 会话 ------------------------------------------------------------------

    def create_session(
        self,
        question: str,
        *,
        seed: int,
        tier: str = "budget",
        anonymous: bool = True,
        workflow: str = "discussion",
        attachments: Sequence[str] = (),
        choice: Mapping[str, Any] | None = None,
    ) -> str:
        """anonymous 在存储层默认开启（更安全）；产品默认值由服务层决定。"""
        session_id = uuid.uuid4().hex
        now = self.clock()
        self._exec(
            "INSERT INTO sessions (id, created_at, updated_at, question, attachments, seed, mode,"
            " status, choice, anonymous, workflow)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)",
            (
                session_id,
                now,
                now,
                question,
                _json(list(attachments)),
                seed,
                tier,
                _json(dict(choice or {"tier": tier})),
                int(anonymous),
                workflow,
            ),
        )
        return session_id

    def set_status(self, session_id: str, status: Status, error: str | None = None) -> None:
        if status not in STATUSES:
            raise ValueError(f"未知状态 {status!r}")
        self._require_session(session_id)
        self._exec(
            "UPDATE sessions SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, self.clock(), session_id),
        )

    def session_row(self, session_id: str) -> dict[str, Any]:
        """内部使用的完整记录（含 seed），不用于对外展示。"""
        row = dict(self._require_session(session_id))
        row["attachments"] = json.loads(row["attachments"])
        row["choice"] = json.loads(row["choice"])
        return row

    def mark_revealed(self, session_id: str) -> None:
        self._require_session(session_id)
        self._exec(
            "UPDATE sessions SET revealed_at = COALESCE(revealed_at, ?), updated_at = ? "
            "WHERE id = ?",
            (self.clock(), self.clock(), session_id),
        )

    def is_revealed(self, session_id: str) -> bool:
        """是否显示身份：匿名关闭时始终显示。"""
        row = self._require_session(session_id)
        return row["revealed_at"] is not None or not row["anonymous"]

    # --- 路由记录 --------------------------------------------------------------

    def save_routing(self, session_id: str, record: RoutingRecord) -> None:
        self._require_session(session_id)
        self._exec(
            "INSERT INTO routing_records (session_id, difficulty, difficulty_source, plan,"
            " estimated_cost_usd, actual_cost_usd, escalated, escalated_plan, record, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(session_id) DO UPDATE SET difficulty = excluded.difficulty,"
            " difficulty_source = excluded.difficulty_source, plan = excluded.plan,"
            " estimated_cost_usd = excluded.estimated_cost_usd,"
            " actual_cost_usd = excluded.actual_cost_usd, escalated = excluded.escalated,"
            " escalated_plan = excluded.escalated_plan, record = excluded.record,"
            " updated_at = excluded.updated_at",
            (
                session_id,
                record.difficulty,
                record.difficulty_source,
                record.plan,
                record.estimated_cost_usd,
                record.actual_cost_usd,
                int(record.escalated),
                record.escalated_plan,
                _json(record.to_dict()),
                self.clock(),
            ),
        )

    def routing_record(self, session_id: str) -> dict[str, Any] | None:
        row = self._exec(
            "SELECT record FROM routing_records WHERE session_id = ?", (session_id,)
        ).fetchone()
        return json.loads(row["record"]) if row else None

    def routing_records(self, limit: int = 1000) -> list[dict[str, Any]]:
        """供调整规则的数据导出（内部使用，含阵容）。"""
        rows = self._exec(
            "SELECT record FROM routing_records ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [json.loads(r["record"]) for r in rows]

    # --- 桌子（执行计划，内部使用） -------------------------------------------------

    def create_table(
        self,
        session_id: str,
        table_no: int,
        *,
        plan: str | None,
        pipeline: Sequence[str],
        members: Mapping[str, str],
        coordinator: str | None,
        escalate_to: str | None,
        estimate: Mapping[str, Any],
        status: str = "pending",
        escalation_reason: str | None = None,
    ) -> None:
        self._require_session(session_id)
        now = self.clock()
        self._exec(
            "INSERT INTO session_tables (session_id, table_no, plan, pipeline, members,"
            " coordinator, escalate_to, estimate, status, escalation_reason, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                table_no,
                plan,
                _json(list(pipeline)),
                # 保持座位顺序（甲、乙、丙…），不能按键排序
                json.dumps([[c, m] for c, m in members.items()], ensure_ascii=False),
                coordinator,
                escalate_to,
                _json(dict(estimate)),
                status,
                escalation_reason,
                now,
                now,
            ),
        )

    def replace_table(self, session_id: str, table_no: int, **fields: Any) -> None:
        """尚未开始的桌子换方案（用户在确认卡片上改选）。"""
        with self._tx():
            self.conn.execute(
                "DELETE FROM session_tables WHERE session_id = ? AND table_no = ?",
                (session_id, table_no),
            )
        self.create_table(session_id, table_no, **fields)

    def set_table_status(self, session_id: str, table_no: int, status: str) -> None:
        self._exec(
            "UPDATE session_tables SET status = ?, updated_at = ?"
            " WHERE session_id = ? AND table_no = ?",
            (status, self.clock(), session_id, table_no),
        )

    def tables(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT * FROM session_tables WHERE session_id = ? ORDER BY table_no", (session_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["pipeline"] = json.loads(d["pipeline"])
            d["members"] = dict(json.loads(d["members"]))
            d["estimate"] = json.loads(d["estimate"])
            out.append(d)
        return out

    def set_budget_override(self, session_id: str) -> None:
        self._require_session(session_id)
        self._exec("UPDATE sessions SET budget_override = 1 WHERE id = ?", (session_id,))

    def budget_override(self, session_id: str) -> bool:
        return bool(self._require_session(session_id)["budget_override"])

    # --- 座位 ------------------------------------------------------------------

    def add_seats(
        self,
        session_id: str,
        table_no: int,
        members: Mapping[str, str],
        coordinator: str | None = None,
    ) -> None:
        """members: 代号 → 模型 id。"""
        self._require_session(session_id)
        now = self.clock()
        with self._tx():
            for code, model_id in members.items():
                self.conn.execute(
                    "INSERT INTO seats (session_id, table_no, role, code, model_id, created_at)"
                    " VALUES (?, ?, 'member', ?, ?, ?)",
                    (session_id, table_no, code, model_id, now),
                )
            if coordinator:
                self.conn.execute(
                    "INSERT INTO seats (session_id, table_no, role, code, model_id, created_at)"
                    " VALUES (?, ?, 'coordinator', NULL, ?, ?)",
                    (session_id, table_no, coordinator, now),
                )

    def seats(self, session_id: str, table_no: int | None = None) -> list[dict[str, Any]]:
        """内部使用（含模型 id）。"""
        sql = "SELECT table_no, role, code, model_id FROM seats WHERE session_id = ?"
        params: list[Any] = [session_id]
        if table_no is not None:
            sql += " AND table_no = ?"
            params.append(table_no)
        return [dict(r) for r in self._exec(sql + " ORDER BY table_no, id", params).fetchall()]

    def recent_coordinators(self, limit: int = 5) -> list[str]:
        """最近担任统筹的模型，最近的在前（供轮换）。"""
        rows = self._exec(
            "SELECT model_id, MAX(id) AS last FROM seats WHERE role = 'coordinator'"
            " GROUP BY model_id ORDER BY last DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["model_id"] for r in rows]

    # --- 调用 ------------------------------------------------------------------

    def record_call(
        self,
        session_id: str,
        *,
        step: str,
        role: Role,
        model_id: str,
        messages: Sequence[Message],
        table_no: int | None = None,
        code: str | None = None,
        prompt: RenderedPrompt | None = None,
        completion: Completion | None = None,
        failure: AllChannelsFailed | NoChannelAvailable | ProviderError | None = None,
    ) -> int:
        if (completion is None) == (failure is None):
            raise ValueError("completion 与 failure 必须且只能提供一个")
        self._require_session(session_id)
        c = completion
        attempts = (
            c.attempts
            if c
            else (failure.attempts if isinstance(failure, AllChannelsFailed) else ())
        )
        with self._tx():
            cur = self.conn.execute(
                "INSERT INTO calls (session_id, table_no, step, role, code, model_id, prompt_role,"
                " prompt_version, prompt_sha256, input, output, channel, channel_kind, route_model,"
                " input_tokens, output_tokens, cached_tokens, cost_usd, cost_source, latency_s,"
                " error, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    table_no,
                    step,
                    role,
                    code,
                    model_id,
                    prompt.role if prompt else None,
                    prompt.version if prompt else None,
                    prompt.sha256 if prompt else None,
                    _json([_message_record(m) for m in messages]),
                    c.text if c else None,
                    c.channel if c else None,
                    c.channel_kind if c else None,
                    c.route_model if c else None,
                    c.input_tokens if c else 0,
                    c.output_tokens if c else 0,
                    c.cached_tokens if c else 0,
                    c.cost_usd if c else 0.0,
                    c.cost_source if c else None,
                    c.latency_s if c else None,
                    str(failure) if failure else None,
                    self.clock(),
                ),
            )
            call_id = cur.lastrowid
            for seq, a in enumerate(attempts):
                self.conn.execute(
                    "INSERT INTO call_attempts (call_id, seq, channel, ok, error, latency_s)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        call_id,
                        seq,
                        a.channel,
                        int(a.ok),
                        a.error.value if a.error else None,
                        a.latency_s,
                    ),
                )
        self._touch(session_id)
        return call_id

    def record_planner(self, session_id: str, result: PlannerResult) -> list[int]:
        """规划员的每次成功调用各记一条。"""
        if not result.model_id:
            return []
        prompt = RenderedPrompt(
            "planner", result.prompt_version, result.prompt_sha256 or "", result.messages
        )
        return [
            self.record_call(
                session_id,
                step="plan",
                role="planner",
                model_id=result.model_id,
                messages=result.messages,
                prompt=prompt,
                completion=c,
            )
            for c in result.completions
        ]

    # --- 产出与进度 --------------------------------------------------------------

    def save_output(
        self,
        session_id: str,
        *,
        table_no: int,
        step: str,
        kind: str,
        content: str | Mapping[str, Any] | Sequence[Any],
        code: str | None = None,
        call_id: int | None = None,
    ) -> int:
        self._require_session(session_id)
        text = content if isinstance(content, str) else _json(content)
        cur = self._exec(
            "INSERT INTO outputs (session_id, table_no, step, kind, code, content, call_id,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, table_no, step, kind, code, text, call_id, self.clock()),
        )
        return cur.lastrowid

    def outputs(
        self, session_id: str, *, table_no: int | None = None, kind: str | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT table_no, step, kind, code, content, call_id FROM outputs WHERE session_id = ?"
        )
        params: list[Any] = [session_id]
        if table_no is not None:
            sql += " AND table_no = ?"
            params.append(table_no)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        return [dict(r) for r in self._exec(sql + " ORDER BY id", params).fetchall()]

    def mark_step_done(self, session_id: str, table_no: int, step: str) -> None:
        self._require_session(session_id)
        self._exec(
            "INSERT OR IGNORE INTO step_progress (session_id, table_no, step, completed_at)"
            " VALUES (?, ?, ?, ?)",
            (session_id, table_no, step, self.clock()),
        )

    def completed_steps(self, session_id: str, table_no: int) -> list[str]:
        rows = self._exec(
            "SELECT step FROM step_progress WHERE session_id = ? AND table_no = ?"
            " ORDER BY completed_at, rowid",
            (session_id, table_no),
        ).fetchall()
        return [r["step"] for r in rows]

    # --- 确认点 ------------------------------------------------------------------

    def create_checkpoint(self, session_id: str, kind: str, card: Mapping[str, Any]) -> int:
        self._require_session(session_id)
        if self.pending_checkpoint(session_id):
            raise ValueError("该会话已有待回复的确认点")
        cur = self._exec(
            "INSERT INTO checkpoints (session_id, kind, card, status, created_at)"
            " VALUES (?, ?, ?, 'pending', ?)",
            (session_id, kind, _json(dict(card)), self.clock()),
        )
        return cur.lastrowid

    def answer_checkpoint(self, checkpoint_id: int, response: str, note: str | None = None) -> None:
        row = self._exec("SELECT status FROM checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
        if row is None:
            raise NotFound(f"确认点 {checkpoint_id} 不存在")
        if row["status"] != "pending":
            raise ValueError("该确认点已经回复过")
        self._exec(
            "UPDATE checkpoints SET status = 'answered', response = ?, note = ?, answered_at = ?"
            " WHERE id = ?",
            (response, note, self.clock(), checkpoint_id),
        )

    def pending_checkpoint(self, session_id: str) -> CheckpointView | None:
        row = self._exec(
            "SELECT * FROM checkpoints WHERE session_id = ? AND status = 'pending'",
            (session_id,),
        ).fetchone()
        return _checkpoint(row) if row else None

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

    def attach_to_session(self, session_id: str, attachment_ids: Sequence[str]) -> None:
        """按给定顺序把上传的文件关联到会话；已属于其他会话的文件不能再用。"""
        self._require_session(session_id)
        with self._tx():
            for position, attachment_id in enumerate(attachment_ids):
                cur = self.conn.execute(
                    "UPDATE attachments SET session_id = ?, position = ?"
                    " WHERE id = ? AND session_id IS NULL",
                    (session_id, position, attachment_id),
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

    # --- 用量 ------------------------------------------------------------------

    def total_spent(self) -> float:
        return self._exec("SELECT COALESCE(SUM(cost_usd), 0) FROM calls").fetchone()[0]

    def spent_between(self, start: datetime, end: datetime) -> float:
        """[start, end) 内的花费（按调用记录时间）。"""
        return self._exec(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE created_at >= ? AND created_at < ?",
            (iso(start), iso(end)),
        ).fetchone()[0]

    def session_cost(self, session_id: str) -> float:
        return self._exec(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE session_id = ?", (session_id,)
        ).fetchone()[0]

    def table_cost(self, session_id: str, table_no: int) -> float:
        return self._exec(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM calls WHERE session_id = ? AND table_no = ?",
            (session_id, table_no),
        ).fetchone()[0]

    def call_samples(self, sessions: int = 30) -> list[dict[str, Any]]:
        """最近若干场讨论中各步骤的调用（成员与统筹，不含规划员），供花费预估用历史校准。"""
        rows = self._exec(
            "SELECT session_id, table_no, step, code, role, model_id, input_tokens,"
            " output_tokens, error FROM calls WHERE table_no IS NOT NULL AND session_id IN"
            " (SELECT id FROM sessions ORDER BY created_at DESC LIMIT ?)",
            (sessions,),
        ).fetchall()
        return [dict(r) for r in rows]

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

    # --- 对外视图 ----------------------------------------------------------------

    def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        rows = self._exec(
            "SELECT s.id, s.created_at, s.question, s.status, s.revealed_at, s.anonymous,"
            " s.workflow,"
            " r.difficulty, r.plan,"
            " (SELECT COALESCE(SUM(cost_usd), 0) FROM calls c WHERE c.session_id = s.id) AS cost"
            " FROM sessions s LEFT JOIN routing_records r ON r.session_id = s.id"
            " ORDER BY s.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SessionSummary(
                r["id"],
                r["created_at"],
                r["question"],
                r["status"],
                r["difficulty"],
                r["plan"],
                r["cost"],
                r["revealed_at"] is not None or not r["anonymous"],
                bool(r["anonymous"]),
                r["workflow"],
            )
            for r in rows
        ]

    def session_view(
        self, session_id: str, *, scrub: Callable[[str, str], str] | None = None
    ) -> SessionView:
        """对外展示。

        匿名开启且未揭晓时隐藏模型、渠道、阵容，scrub(text, question) 遮蔽输出中的身份；
        匿名关闭时始终显示全部原文。
        """
        s = self._require_session(session_id)
        revealed = s["revealed_at"] is not None or not s["anonymous"]
        question = s["question"]

        def clean(text: str) -> str:
            return text if revealed or scrub is None else scrub(text, question)

        record = self.routing_record(session_id)
        if record is not None and not revealed:
            record = {k: v for k, v in record.items() if k not in _ROUTING_PRIVATE}

        seats = tuple(
            SeatView(r["table_no"], r["role"], r["code"], r["model_id"] if revealed else None)
            for r in self.seats(session_id)
        )
        outputs = tuple(
            OutputView(o["table_no"], o["step"], o["kind"], o["code"], clean(o["content"]))
            for o in self.outputs(session_id)
        )

        attempts: dict[int, list[dict[str, Any]]] = {}
        if revealed:
            for a in self._exec(
                "SELECT a.* FROM call_attempts a JOIN calls c ON c.id = a.call_id"
                " WHERE c.session_id = ? ORDER BY a.call_id, a.seq",
                (session_id,),
            ).fetchall():
                attempts.setdefault(a["call_id"], []).append(
                    {"channel": a["channel"], "ok": bool(a["ok"]), "error": a["error"]}
                )
        calls = tuple(
            CallView(
                id=c["id"],
                table_no=c["table_no"],
                step=c["step"],
                role=c["role"],
                code=c["code"],
                input_tokens=c["input_tokens"],
                output_tokens=c["output_tokens"],
                cost_usd=c["cost_usd"],
                latency_s=c["latency_s"],
                failed=c["error"] is not None,
                # 错误信息里有渠道名，揭晓前只给出通用提示
                error=(c["error"] if revealed else HIDDEN_ERROR) if c["error"] else None,
                model_id=c["model_id"] if revealed else None,
                channel=c["channel"] if revealed else None,
                attempts=tuple(attempts.get(c["id"], ())),
            )
            for c in self._exec(
                "SELECT * FROM calls WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        )
        checkpoints = tuple(
            _checkpoint(r)
            for r in self._exec(
                "SELECT * FROM checkpoints WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
        )
        return SessionView(
            id=s["id"],
            created_at=s["created_at"],
            question=question,
            status=s["status"],
            tier=s["mode"],
            workflow=s["workflow"],
            anonymous=bool(s["anonymous"]),
            revealed=revealed,
            routing=record,
            seats=seats,
            outputs=outputs,
            calls=calls,
            checkpoints=checkpoints,
            cost_usd=self.session_cost(session_id),
            error=s["error"] if revealed or s["error"] is None else HIDDEN_ERROR,
        )


def _attachment(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["warnings"] = json.loads(data["warnings"])
    return data


def _message_record(m: Message) -> dict[str, Any]:
    """存库的消息：图片 / 音频只记类型、大小和哈希，不存内容。"""
    record: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.media:
        record["media"] = [x.describe() for x in m.media]
    return record


def _checkpoint(row: sqlite3.Row) -> CheckpointView:
    return CheckpointView(
        row["id"], row["kind"], json.loads(row["card"]), row["status"], row["response"], row["note"]
    )
