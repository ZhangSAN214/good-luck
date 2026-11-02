"""超级电脑的审计账：每条操作一行，前后用哈希串成链（docs/SUPERCOMPUTER.md「管家」）。

存在 ``data/super/audit.jsonl``，只追加不改。每行::

    {"n": 序号, "at": 时间, "act": 动作, "data": {...}, "prev": 上一行的 hash, "hash": 本行 hash}

``hash`` = sha256（n、at、act、data、prev 按固定格式排好的 JSON）。改了中间任何一行、删了一行、
调换了顺序，``verify()`` 都能指出从第几条开始对不上。

哈希链能发现「被改过」，但整条重算一遍就能骗过它；所以每天的日报还会记下当天最后一条的序号和
hash（锚点，存在 butler.json），校验时一并对照：日报写过的锚点和链上不一样，也算被改过。

写进来的文字先脱敏（已加载的 key 和疑似 key 换成 ***）。没有本机目录（内存数据库）时只记在内存里。
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from roundtable.core.providers.secrets import redact

GENESIS = "0" * 64
MAX_TEXT = 500  # 每段文字最多记多少字（交的活很长时只记开头）


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _clean(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)[:MAX_TEXT]
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_clean(v) for v in value]
    if value is None or isinstance(value, bool | int | float):
        return value
    return _clean(str(value))


def digest(n: int, at: str, act: str, data: Mapping[str, Any], prev: str) -> str:
    body = json.dumps(
        {"n": n, "at": at, "act": act, "data": data, "prev": prev},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class AuditLog:
    def __init__(self, path: Path | None, *, now: Callable[[], datetime] = _utcnow) -> None:
        self.path = path
        self.now = now
        self._memory: list[dict[str, Any]] = []
        self.count = 0
        self.head = GENESIS
        last = self._last_line()
        if last is not None:
            self.count, self.head = int(last.get("n", 0)), str(last.get("hash", GENESIS))

    def _last_line(self) -> dict[str, Any] | None:
        if self.path is None or not self.path.is_file():
            return None
        last = None
        with self.path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return None
        try:
            got = json.loads(last)
        except ValueError:
            return None  # 最后一行坏了：接着写，verify 会指出来
        return got if isinstance(got, dict) else None

    def append(self, act: str, **data: Any) -> dict[str, Any]:
        n = self.count + 1
        at = self.now().astimezone(UTC).isoformat(timespec="seconds")
        clean = _clean(data)
        entry = {"n": n, "at": at, "act": act, "data": clean, "prev": self.head}
        entry["hash"] = digest(n, at, act, clean, self.head)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        if self.path is None:
            self._memory.append(entry)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
        self.count, self.head = n, entry["hash"]
        return entry

    def entries(self) -> list[dict[str, Any]]:
        """全部记录（坏掉的行原样跳过；校验用 verify）。"""
        if self.path is None:
            return list(self._memory)
        if not self.path.is_file():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                got = json.loads(line)
            except ValueError:
                continue
            if isinstance(got, dict):
                out.append(got)
        return out

    def since(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """[start, end) 之间的记录（按记录里的时间）。"""
        out = []
        for e in self.entries():
            try:
                at = datetime.fromisoformat(str(e.get("at")))
            except ValueError:
                continue
            if start <= at < end:
                out.append(e)
        return out

    def verify(self, anchors: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
        """从头逐条重算。返回 {ok, count, head, broken_at, why}；broken_at 是第一条对不上的序号。"""
        lines: list[str]
        if self.path is None:
            lines = [json.dumps(e, ensure_ascii=False) for e in self._memory]
        elif self.path.is_file():
            lines = [x for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]
        else:
            lines = []
        prev, hashes = GENESIS, {}
        total = len(lines)
        for i, line in enumerate(lines, 1):
            try:
                e = json.loads(line)
                n, at, act, data = e["n"], e["at"], e["act"], e["data"]
                stored_prev, stored = e["prev"], e["hash"]
            except (ValueError, KeyError, TypeError):
                return _broken(i, "这一行不是完整的记录", total, prev)
            if n != i:
                return _broken(
                    i, f"序号应该是 {i}，写的是 {n}（有记录被删掉或调换了）", len(lines), prev
                )
            if stored_prev != prev:
                return _broken(i, "和上一条接不上（上一条被改过、删掉或调换了）", total, prev)
            if digest(n, at, act, data, prev) != stored:
                return _broken(i, "内容和它的 hash 对不上（这一条被改过）", total, prev)
            prev, hashes[n] = stored, stored
        for day, anchor in sorted((anchors or {}).items()):
            n = int(anchor.get("n", 0))
            if n == 0:
                continue
            if n > len(lines):
                return _broken(
                    n, f"{day} 的日报记到第 {n} 条，链上只剩 {len(lines)} 条", len(lines), prev
                )
            if hashes.get(n) != anchor.get("hash"):
                return _broken(
                    n, f"和 {day} 日报记下的锚点不一样（整条链被重写过）", len(lines), prev
                )
        return {"ok": True, "count": len(lines), "head": prev, "broken_at": None, "why": None}


def _broken(n: int, why: str, count: int, head: str) -> dict[str, Any]:
    return {"ok": False, "count": count, "head": head, "broken_at": n, "why": why}
