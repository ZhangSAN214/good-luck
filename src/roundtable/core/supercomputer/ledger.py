"""超级电脑的账本：每位助手今天派了几件、冷却到什么时候、界面上改过的每天次数。

存在 ``data/super/ledger.json``；日期按 UTC 算（和预算一致）。没有目录（内存数据库）时只在内存里。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class Ledger:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.data: dict[str, Any] = {"day": "", "used": {}, "cooldown": {}, "limits": {}}
        if path is not None and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = {}
            if isinstance(loaded, dict):
                for key in self.data:
                    if isinstance(loaded.get(key), type(self.data[key])):
                        self.data[key] = loaded[key]

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _roll(self, now: datetime) -> None:
        day = now.astimezone(UTC).date().isoformat()
        if self.data["day"] != day:
            self.data["day"], self.data["used"] = day, {}

    def used(self, worker: str, now: datetime) -> int:
        self._roll(now)
        return int(self.data["used"].get(worker, 0))

    def add(self, worker: str, now: datetime) -> None:
        self._roll(now)
        self.data["used"][worker] = self.used(worker, now) + 1
        self._save()

    def limit(self, worker: str, default: int) -> int:
        return int(self.data["limits"].get(worker, default))

    def set_limit(self, worker: str, value: int) -> None:
        self.data["limits"][worker] = int(value)
        self._save()

    def cooling(self, worker: str, now: datetime) -> datetime | None:
        raw = self.data["cooldown"].get(worker)
        if not raw:
            return None
        until = datetime.fromisoformat(raw)
        return until if until > now else None

    def cool(self, worker: str, until: datetime) -> None:
        self.data["cooldown"][worker] = until.isoformat()
        self._save()

    def clear_cooldown(self, worker: str) -> None:
        self.data["cooldown"].pop(worker, None)
        self._save()
