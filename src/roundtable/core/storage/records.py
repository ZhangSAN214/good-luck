"""数据访问层共用的类型、常量和对外视图的数据结构（从 repository.py 拆出，代码精简）。"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from roundtable.core.providers import Message

Status = Literal[
    "created", "awaiting_confirmation", "running", "paused", "completed", "failed", "stopped"
]


STATUSES = frozenset(Status.__args__)  # type: ignore[attr-defined]


Role = Literal["planner", "member", "coordinator", "preprocess", "tool", "media"]
# preprocess：附件预处理；tool：工具中的模型调用（如图像生成）


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


@dataclass(frozen=True)
class CheckpointView:
    id: int
    kind: str
    card: dict[str, Any]
    status: str
    response: str | None
    note: str | None


@dataclass
class ChannelUsage:
    calls: int = 0
    failed_attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


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
