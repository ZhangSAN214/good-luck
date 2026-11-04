"""数据访问。其他模块不直接写 SQL。

对外展示一律走 session_view()：揭晓前不返回模型 id、渠道、切换记录，
并可传入身份遮蔽器处理模型输出（防止输出里的自报身份泄露给用户）。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from roundtable.core.storage.records import (
    STATUSES,
    ChannelUsage,
    CheckpointView,
    NotFound,
    Role,
    Status,
    _checkpoint,
    _now,
    iso,
)
from roundtable.core.storage.repo.chat import ChatRepo
from roundtable.core.storage.repo.files import FilesRepo
from roundtable.core.storage.repo.mail import MailRepo
from roundtable.core.storage.repo.moments import MomentsRepo
from roundtable.core.storage.repo.office import OfficeRepo
from roundtable.core.storage.repo.sessions import SessionsRepo

HIDDEN_ERROR = "出错（详情揭晓后可见）"


STATE_KEY = "member_state:"  # 成员状态（P3）在 app_settings 里的键前缀
ABOUT_KEY = "about_you:"  # 关于你（P8）在 app_settings 里的键前缀：画像、开关
GROWTH_KEY = "member_growth:"  # 成员成长（P6）在 app_settings 里的键前缀
RELATION_KEY = "member_relation:"  # 成员关系（P4）在 app_settings 里的键前缀
RELATION_TRACKER = "relation.tracker"  # 关系插件认「一来一回」用的房间游标（P4）


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


# 揭晓前从路由记录中去掉的字段（阵容与规划员模型都是身份信息）
_ROUTING_PRIVATE = (
    "members",
    "coordinator",
    "absent",
    "planner_model",
    "planner_error",
    "extra",
)


class _Rows:
    """锁内已取完的查询结果；用法和 sqlite3.Cursor 相同（fetchone / fetchall / 遍历）。"""

    __slots__ = ("_rows", "_pos", "lastrowid", "rowcount")

    def __init__(self, rows: list[sqlite3.Row], lastrowid: int | None, rowcount: int) -> None:
        self._rows = rows
        self._pos = 0
        self.lastrowid = lastrowid
        self.rowcount = rowcount

    def fetchone(self) -> sqlite3.Row | None:
        if self._pos >= len(self._rows):
            return None
        row = self._rows[self._pos]
        self._pos += 1
        return row

    def fetchall(self) -> list[sqlite3.Row]:
        rest = self._rows[self._pos :]
        self._pos = len(self._rows)
        return rest

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


@dataclass
class Repository(SessionsRepo, FilesRepo, ChatRepo, MomentsRepo, MailRepo, OfficeRepo):
    conn: sqlite3.Connection
    clock: Callable[[], str] = _now
    _lock: threading.RLock = field(default_factory=threading.RLock)

    # --- 内部 ------------------------------------------------------------------

    def _exec(self, sql: str, params: Sequence[Any] = ()) -> _Rows:
        """执行并在锁内取完全部结果。

        连接是多线程共用的（服务线程、后台任务、测试线程）。sqlite3 的游标是边取边读的：
        只把 execute 放进锁、fetchone 留在锁外，别的线程同时在这条连接上执行或开事务时，
        会读到 None（行明明在）或读串。所以结果在锁内一次取完再交出去。
        """
        with self._lock:
            cur = self.conn.execute(sql, params)
            return _Rows(cur.fetchall(), cur.lastrowid, cur.rowcount)

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

    # --- 成员状态（P3；app_settings 的 member_state:<成员> 键，只有 member/state.py 写）

    def member_state(self, member: str) -> dict[str, Any] | None:
        row = self._exec(
            "SELECT value, updated_at FROM app_settings WHERE key = ?", (STATE_KEY + member,)
        ).fetchone()
        return None if row is None else {"data": json.loads(row["value"]), "at": row["updated_at"]}

    def save_member_state(self, member: str, data: dict[str, Any]) -> None:
        self.set_setting(STATE_KEY + member, json.dumps(data, ensure_ascii=False))

    def clear_member_state(self, member: str | None = None) -> int:
        if member is None:
            return self._exec(
                "DELETE FROM app_settings WHERE key LIKE ?", (STATE_KEY + "%",)
            ).rowcount
        return self._exec("DELETE FROM app_settings WHERE key = ?", (STATE_KEY + member,)).rowcount

    # --- 关于你（P8；app_settings 的 about_you:<名字> 键，只有 member/about.py 写）

    def about_you(self, name: str) -> dict[str, Any] | None:
        row = self._exec(
            "SELECT value FROM app_settings WHERE key = ?", (ABOUT_KEY + name,)
        ).fetchone()
        return None if row is None else json.loads(row["value"])

    def save_about_you(self, name: str, data: dict[str, Any]) -> None:
        self.set_setting(ABOUT_KEY + name, json.dumps(data, ensure_ascii=False))

    def clear_about_you(self, name: str | None = None) -> int:
        if name is None:
            return self._exec(
                "DELETE FROM app_settings WHERE key LIKE ?", (ABOUT_KEY + "%",)
            ).rowcount
        return self._exec("DELETE FROM app_settings WHERE key = ?", (ABOUT_KEY + name,)).rowcount

    # --- 成员成长（P6；app_settings 的 member_growth:<成员> 键，只有 member/growth 写）

    def growth_data(self, member: str) -> dict[str, Any] | None:
        value = self.setting(GROWTH_KEY + member)
        return None if value is None else json.loads(value)

    def save_growth_data(self, member: str, data: dict[str, Any]) -> None:
        self.set_setting(GROWTH_KEY + member, json.dumps(data, ensure_ascii=False))

    def clear_growth_data(self, member: str | None = None) -> int:
        if member is None:
            return self._exec(
                "DELETE FROM app_settings WHERE key LIKE ?", (GROWTH_KEY + "%",)
            ).rowcount
        return self._exec("DELETE FROM app_settings WHERE key = ?", (GROWTH_KEY + member,)).rowcount

    # --- 成员关系（P4；app_settings 的 member_relation:<成员> 键，只有 member/relation.py 写）

    def relation_data(self, member: str) -> dict[str, Any] | None:
        value = self.setting(RELATION_KEY + member)
        return None if value is None else json.loads(value)

    def save_relation_data(self, member: str, data: dict[str, Any]) -> None:
        self.set_setting(RELATION_KEY + member, json.dumps(data, ensure_ascii=False))

    def relation_tracker(self) -> dict[str, Any]:
        value = self.setting(RELATION_TRACKER)
        return {} if value is None else json.loads(value)

    def save_relation_tracker(self, data: dict[str, Any]) -> None:
        self.set_setting(RELATION_TRACKER, json.dumps(data, ensure_ascii=False))

    def clear_relation_data(self, member: str | None = None) -> int:
        if member is None:
            self._exec("DELETE FROM app_settings WHERE key = ?", (RELATION_TRACKER,))
            return self._exec(
                "DELETE FROM app_settings WHERE key LIKE ?", (RELATION_KEY + "%",)
            ).rowcount
        return self._exec(
            "DELETE FROM app_settings WHERE key = ?", (RELATION_KEY + member,)
        ).rowcount

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

    # --- 对外视图 ----------------------------------------------------------------

    def list_sessions(self, limit: int = 50) -> list[SessionSummary]:
        rows = self._exec(
            "SELECT s.id, s.created_at, s.question, s.status, s.revealed_at, s.anonymous,"
            " s.workflow,"
            " r.difficulty, r.plan,"
            " (SELECT COALESCE(SUM(cost_usd), 0) FROM calls c WHERE c.session_id = s.id) AS cost"
            " FROM sessions s LEFT JOIN routing_records r ON r.session_id = s.id"
            " WHERE s.workflow NOT IN"
            " ('chat', 'evolution_scout', 'mail', 'watch', 'voice', 'sticker')"
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


__all__ = [  # 原来都在这里定义，搬到 records.py 后照旧可以从这里导入
    "ABOUT_KEY",
    "CallView",
    "ChannelUsage",
    "CheckpointView",
    "GROWTH_KEY",
    "HIDDEN_ERROR",
    "NotFound",
    "OutputView",
    "RELATION_KEY",
    "RELATION_TRACKER",
    "Repository",
    "Role",
    "STATE_KEY",
    "STATUSES",
    "SeatView",
    "SessionSummary",
    "SessionView",
    "Status",
    "iso",
]
