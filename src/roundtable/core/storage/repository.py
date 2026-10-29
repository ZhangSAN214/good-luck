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
Role = Literal["planner", "member", "coordinator", "preprocess", "tool", "media"]
# preprocess：附件预处理；tool：工具中的模型调用（如图像生成）
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

    def pause_orphaned(self, error: str) -> list[str]:
        """开机对账：把上次进程退出时还停在 running 的会话改成 paused，返回这些会话的 id。

        只在服务启动、还没有任何后台任务时调用；等待确认的会话本来就在等用户，不动。
        """
        rows = self._exec("SELECT id FROM sessions WHERE status = 'running' ORDER BY id").fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            self._exec(
                "UPDATE sessions SET status = 'paused', error = ?, updated_at = ?"
                f" WHERE status = 'running' AND id IN ({', '.join('?' * len(ids))})",
                (error, self.clock(), *ids),
            )
        return ids

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

    def working_seats(self, since: datetime) -> list[dict[str, Any]]:
        """进行中（running / awaiting_confirmation）且 since 之后还有更新的会话的座位（内部使用）。

        hidden 为真表示匿名开启且未揭晓：调用方不得把这些座位对应到具体模型展示。
        """
        rows = self._exec(
            "SELECT s.id AS session_id, s.status, s.workflow, st.model_id,"
            " st.table_no, st.role, st.code,"
            " (s.anonymous = 1 AND s.revealed_at IS NULL) AS hidden"
            " FROM sessions s JOIN seats st ON st.session_id = s.id"
            " WHERE s.status IN ('running', 'awaiting_confirmation') AND s.updated_at >= ?"
            " ORDER BY s.updated_at DESC, st.id",
            (iso(since),),
        ).fetchall()
        return [{**dict(r), "hidden": bool(r["hidden"])} for r in rows]

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
                " error, created_at, finish_reason, reasoning_tokens)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    c.finish_reason if c else None,
                    c.reasoning_tokens if c else 0,
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

    def record_panel(self, session_id: str, result: Any) -> list[int]:
        """分诊小组的每次成功调用各记一条（step=triage，计入花费与预算）。"""
        return [
            self.record_call(
                session_id,
                step="triage",
                role="planner",
                model_id=c.model_id,
                messages=c.messages,
                prompt=RenderedPrompt(
                    "panel", result.prompt_version, result.prompt_sha256 or "", c.messages
                ),
                completion=c.completion,
            )
            for c in result.calls
        ]

    def update_session_choice(self, session_id: str, choice: Mapping[str, Any]) -> None:
        """分诊建议被用户确认后，把"自动"改成具体的作答方式与档位。"""
        self._require_session(session_id)
        self._exec(
            "UPDATE sessions SET choice = ?, workflow = ?, mode = ?, updated_at = ? WHERE id = ?",
            (
                _json(dict(choice)),
                choice["workflow"],
                choice.get("tier") or "budget",
                self.clock(),
                session_id,
            ),
        )

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

    # --- 对比实验 -----------------------------------------------------------------

    def add_bench_run(
        self,
        *,
        batch: str,
        question_id: str,
        category: str,
        workflow: str,
        tier: str,
        session_id: str | None,
        status: str,
        correct: bool | None,
        cost_usd: float,
        seconds: float,
    ) -> int:
        with self._tx():
            cur = self.conn.execute(
                "INSERT INTO bench_runs (batch, question_id, category, workflow, tier, session_id,"
                " status, correct, cost_usd, seconds, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    batch,
                    question_id,
                    category,
                    workflow,
                    tier,
                    session_id,
                    status,
                    None if correct is None else int(correct),
                    cost_usd,
                    seconds,
                    _now(),
                ),
            )
        return int(cur.lastrowid or 0)

    def bench_runs(self, batch: str) -> list[dict[str, Any]]:
        rows = self._exec("SELECT * FROM bench_runs WHERE batch = ? ORDER BY id", (batch,))
        return [dict(r) for r in rows.fetchall()]

    def bench_batches(self) -> list[dict[str, Any]]:
        """历次实验：最近的在前。"""
        rows = self._exec(
            "SELECT batch, COUNT(*) AS runs, COALESCE(SUM(cost_usd), 0) AS cost_usd,"
            " MIN(created_at) AS started_at FROM bench_runs GROUP BY batch"
            " ORDER BY MAX(id) DESC"
        )
        return [dict(r) for r in rows.fetchall()]

    # --- 用户中途插话 -------------------------------------------------------------

    def add_note(self, session_id: str, text: str) -> int:
        """登记一条用户补充；下一个步骤开始时被带上。返回编号。"""
        self._require_session(session_id)
        with self._tx():
            cur = self.conn.execute(
                "INSERT INTO user_notes (session_id, text, created_at) VALUES (?, ?, ?)",
                (session_id, text, _now()),
            )
        return int(cur.lastrowid or 0)

    def pending_notes(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT id, text, created_at FROM user_notes"
            " WHERE session_id = ? AND applied_step IS NULL ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_notes_applied(
        self, session_id: str, note_ids: Sequence[int], table_no: int, step: str
    ) -> None:
        with self._tx():
            for note_id in note_ids:
                self.conn.execute(
                    "UPDATE user_notes SET applied_table = ?, applied_step = ?, applied_at = ?"
                    " WHERE id = ? AND session_id = ? AND applied_step IS NULL",
                    (table_no, step, _now(), note_id, session_id),
                )

    def applied_notes(self, session_id: str) -> list[str]:
        """已经被带上的补充（按时间顺序）；之后的每个步骤都带着它们。"""
        rows = self._exec(
            "SELECT text FROM user_notes WHERE session_id = ? AND applied_step IS NOT NULL"
            " ORDER BY id",
            (session_id,),
        ).fetchall()
        return [r["text"] for r in rows]

    def notes(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._exec(
            "SELECT id, text, created_at, applied_table, applied_step FROM user_notes"
            " WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

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

    def mark_recalled(self, notes: dict[int, str]) -> None:
        """记下这些记忆这次被想起了、因为什么（不改 updated_at 和 source）。"""
        now = self.clock()
        for memory_id, note in notes.items():
            self._exec(
                "UPDATE member_memories SET recalled_at = ?, recall_note = ? WHERE id = ?",
                (now, note, memory_id),
            )

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

    def spent_in_steps(
        self, workflow: str, steps: Sequence[str], start: datetime, end: datetime
    ) -> float:
        """[start, end) 内某类隐藏会话里、指定步骤的调用花费（如生活引擎的 life / memory）。"""
        marks = ", ".join("?" for _ in steps)
        return self._exec(
            "SELECT COALESCE(SUM(c.cost_usd), 0) FROM calls c JOIN sessions s"
            f" ON s.id = c.session_id WHERE s.workflow = ? AND c.step IN ({marks})"
            " AND c.created_at >= ? AND c.created_at < ?",
            (workflow, *steps, iso(start), iso(end)),
        ).fetchone()[0]

    def count_in_steps(
        self, workflow: str, steps: Sequence[str], start: datetime, end: datetime
    ) -> int:
        """[start, end) 内某类隐藏会话里、指定步骤的调用次数（如每天的联网搜索次数）。"""
        marks = ", ".join("?" for _ in steps)
        return self._exec(
            "SELECT COUNT(*) FROM calls c JOIN sessions s ON s.id = c.session_id"
            f" WHERE s.workflow = ? AND c.step IN ({marks})"
            " AND c.created_at >= ? AND c.created_at < ?",
            (workflow, *steps, iso(start), iso(end)),
        ).fetchone()[0]

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

    def moments_by(self, author: str, since: str) -> int:
        return self._exec(
            "SELECT COUNT(*) FROM moments WHERE author = ? AND created_at >= ?", (author, since)
        ).fetchone()[0]

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

    def spent_in_workflow(self, workflow: str, start: datetime, end: datetime) -> float:
        """[start, end) 内挂在某类隐藏会话（如 workflow = 'chat'）上的调用花费。"""
        return self._exec(
            "SELECT COALESCE(SUM(c.cost_usd), 0) FROM calls c JOIN sessions s"
            " ON s.id = c.session_id WHERE s.workflow = ? AND c.created_at >= ?"
            " AND c.created_at < ?",
            (workflow, iso(start), iso(end)),
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

    # --- 对外视图 ----------------------------------------------------------------

    def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        rows = self._exec(
            "SELECT s.id, s.created_at, s.question, s.status, s.revealed_at, s.anonymous,"
            " s.workflow,"
            " r.difficulty, r.plan,"
            " (SELECT COALESCE(SUM(cost_usd), 0) FROM calls c WHERE c.session_id = s.id) AS cost"
            " FROM sessions s LEFT JOIN routing_records r ON r.session_id = s.id"
            " WHERE s.workflow NOT IN ('chat', 'evolution_scout', 'mail', 'watch')"
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


def _chat_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["meta"] = json.loads(out["meta"]) if out.get("meta") else {}
    return out


def _room_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = ("id", "kind", "title", "members", "with_user", "last_read", "created_at")
    out = {k: row[k] for k in keys}
    out["members"] = json.loads(out["members"] or "[]")
    out["with_user"] = bool(out["with_user"])
    return out


def _memory_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["offline"] = bool(out["offline"])
    return out


def _moment_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["meta"] = json.loads(out["meta"]) if out["meta"] else None
    out["offline"] = bool(out["offline"])
    return out


def _web_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["topics"] = json.loads(out["topics"])
    out["unverified"] = bool(out["unverified"])
    return out


def _life_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["members"] = json.loads(out["members"])
    out["written"] = bool(out["written"])
    out["offline"] = bool(out["offline"])
    return out


def _mail_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in (
        "is_read",
        "flagged",
        "high",
        "attachments",
        "has_body",
        "has_summary",
        "has_translation",
        "has_html",
    ):
        if key in out:
            out[key] = bool(out[key])
    return out
