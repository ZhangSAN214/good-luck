"""会话：路由记录、桌子、座位、调用、产出、确认点、对比实验、中途插话。

从 repository.py 拆出（代码精简）。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from roundtable.core.prompts import RenderedPrompt
from roundtable.core.providers import (
    AllChannelsFailed,
    Completion,
    Message,
    NoChannelAvailable,
    ProviderError,
)
from roundtable.core.routing import PlannerResult, RoutingRecord
from roundtable.core.storage.records import (
    STATUSES,
    CheckpointView,
    NotFound,
    Role,
    Status,
    _checkpoint,
    _json,
    _message_record,
    _now,
    iso,
)


class SessionsRepo:
    """会话：路由记录、桌子、座位、调用、产出、确认点、对比实验、中途插话。"""

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
