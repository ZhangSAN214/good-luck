"""整库备份（docs/BACKUP.md）：成员的记忆、关系、日记、成长时间线、聊天记录都在一个 SQLite 文件里，
这里每天自动备份一份，并在试跑、更新、恢复之前存一个「存档点」，坏了能一键恢复。

- **每天一份**：每个本地日期第一次推进时备份（电脑那天没开就跳过），留 ``keep_daily`` 份。
- **存档点**：手动或在大操作前存，带一个短标签，留 ``keep_checkpoints`` 份，和每天的分开数。
- **恢复**：先校验备份（能打开、完整性检查通过、版本不比代码新），再自动存一个「恢复前」存档点，
  然后用 SQLite 在线备份接口把备份整个写回正在用的数据库。恢复后要重启圆桌，让各模块重新读取。
- 用 SQLite 自己的在线备份接口（``Connection.backup``），服务开着也能备份，不会备出半截文件；
  先写临时文件再改名，断电也不会留下坏的备份。
- 只备份数据库本身；上传的图片、语音等大文件不在里面（docs/BACKUP.md 写了怎么手动备份）。
- 文件名只认本模块自己生成的格式，恢复时按名字在备份目录里找，不接受路径。
"""

from __future__ import annotations

import contextlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from roundtable.core.storage.db import current_version, migrate
from roundtable.core.storage.migrations import MIGRATIONS

DAILY = "daily"
CHECKPOINT = "checkpoint"
PREFIX = "roundtable"
SUFFIX = ".db"
BEFORE_RESTORE = "恢复前"
# roundtable-daily-20261101.db / roundtable-checkpoint-20261101-031500-更新前.db
_DAILY = re.compile(rf"^{PREFIX}-{DAILY}-(\d{{8}}){re.escape(SUFFIX)}$")
_POINT = re.compile(
    rf"^{PREFIX}-{CHECKPOINT}-(\d{{8}}-\d{{6}})-([\w-]{{1,24}}){re.escape(SUFFIX)}$"
)
_LABEL = re.compile(r"[^\w-]+")


class BackupError(RuntimeError):
    """备份或恢复失败（信息是给用户看的大白话）。"""


@dataclass(frozen=True)
class BackupFile:
    name: str
    kind: str  # daily / checkpoint
    at: datetime
    label: str
    size: int

    def view(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "at": self.at.isoformat(timespec="seconds"),
            "label": self.label,
            "size": self.size,
        }


def clean_label(label: str) -> str:
    """标签只留字母、数字、汉字、下划线和连字符，最多 24 个字；空了就叫「手动」。"""
    out = _LABEL.sub("-", label.strip()).strip("-")[:24].strip("-")
    return out or "手动"


def parse_name(name: str, size: int = 0) -> BackupFile | None:
    m = _DAILY.match(name)
    if m:
        at = datetime.strptime(m.group(1), "%Y%m%d")
        return BackupFile(name, DAILY, at, "", size)
    m = _POINT.match(name)
    if m:
        at = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S")
        return BackupFile(name, CHECKPOINT, at, m.group(2), size)
    return None


class DbBackup:
    """``conn`` 是正在用的数据库连接；``folder`` 为空（内存数据库）时什么都不做。"""

    def __init__(
        self,
        conn: sqlite3.Connection,
        folder: Path | None,
        *,
        enabled: bool = True,
        keep_daily: int = 14,
        keep_checkpoints: int = 10,
    ) -> None:
        self.conn = conn
        self.folder = folder
        self.enabled = enabled and folder is not None
        self.keep_daily = keep_daily
        self.keep_checkpoints = keep_checkpoints

    # ---- 查看 ----

    def files(self) -> list[BackupFile]:
        """新的在前。"""
        if self.folder is None or not self.folder.is_dir():
            return []
        out = []
        for p in self.folder.iterdir():
            f = parse_name(p.name, p.stat().st_size if p.is_file() else 0)
            if f is not None and p.is_file():
                out.append(f)
        return sorted(out, key=lambda f: (f.at, f.name), reverse=True)

    def status(self) -> dict[str, Any]:
        files = self.files()
        return {
            "enabled": self.enabled,
            "folder": str(self.folder) if self.folder else None,
            "keep_daily": self.keep_daily,
            "keep_checkpoints": self.keep_checkpoints,
            "files": [f.view() for f in files],
            "latest": files[0].view() if files else None,
        }

    # ---- 备份 ----

    def daily(self, now: datetime) -> BackupFile | None:
        """今天还没备份就备一份；已经有了返回 None。"""
        if not self.enabled:
            return None
        name = f"{PREFIX}-{DAILY}-{now:%Y%m%d}{SUFFIX}"
        assert self.folder is not None
        if (self.folder / name).exists():
            return None
        got = self._write(name)
        self._prune(DAILY, self.keep_daily)
        return got

    def checkpoint(self, label: str, now: datetime) -> BackupFile:
        """存一个存档点（备份关着也能手动存）。"""
        if self.folder is None:
            raise BackupError("内存数据库不能备份")
        name = f"{PREFIX}-{CHECKPOINT}-{now:%Y%m%d-%H%M%S}-{clean_label(label)}{SUFFIX}"
        got = self._write(name)
        self._prune(CHECKPOINT, self.keep_checkpoints)
        return got

    def _write(self, name: str) -> BackupFile:
        assert self.folder is not None
        self.folder.mkdir(parents=True, exist_ok=True)
        final = self.folder / name
        tmp = final.with_name(final.name + ".part")
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        dst = sqlite3.connect(str(tmp))
        try:
            self.conn.backup(dst)
        except sqlite3.Error as exc:
            dst.close()
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            raise BackupError(f"备份没写成：{exc}") from None
        dst.close()
        os.replace(tmp, final)
        got = parse_name(name, final.stat().st_size)
        assert got is not None
        return got

    def _prune(self, kind: str, keep: int) -> None:
        assert self.folder is not None
        old = [f for f in self.files() if f.kind == kind][keep:]
        for f in old:
            with contextlib.suppress(FileNotFoundError):
                (self.folder / f.name).unlink()

    # ---- 恢复 ----

    def check(self, name: str) -> BackupFile:
        """按名字找到备份并校验；不对就报错，不动正在用的数据库。"""
        f = parse_name(name)
        if f is None or self.folder is None:
            raise BackupError("没有这份备份")
        path = self.folder / f.name
        if not path.is_file():
            raise BackupError("没有这份备份")
        try:
            src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        except sqlite3.Error:
            raise BackupError("这份备份打不开") from None
        try:
            ok = src.execute("PRAGMA integrity_check").fetchone()
            if not ok or ok[0] != "ok":
                raise BackupError("这份备份已损坏（完整性检查没通过）")
            has = src.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if not has:
                raise BackupError("这不是圆桌的数据库")
            version = src.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
        except sqlite3.DatabaseError as exc:
            raise BackupError(f"这份备份读不了：{exc}") from None
        finally:
            src.close()
        latest = MIGRATIONS[-1][0] if MIGRATIONS else 0
        if version > latest:
            raise BackupError(f"这份备份的版本 {version} 比程序支持的 {latest} 新，请先更新圆桌")
        return parse_name(f.name, path.stat().st_size) or f

    def restore(self, name: str, now: datetime) -> dict[str, Any]:
        """把一份备份整个写回正在用的数据库。先校验，再自动存「恢复前」存档点。"""
        f = self.check(name)
        before = self.checkpoint(BEFORE_RESTORE, now)
        assert self.folder is not None
        src = sqlite3.connect(f"file:{(self.folder / f.name).as_posix()}?mode=ro", uri=True)
        try:
            src.backup(self.conn)
        except sqlite3.Error as exc:
            raise BackupError(f"恢复没成功，正在用的数据库没动：{exc}") from None
        finally:
            src.close()
        migrate(self.conn)  # 旧备份比现在的表结构旧：补上之后的迁移
        return {
            "restored": f.view(),
            "before": before.view(),
            "version": current_version(self.conn),
            "restart": True,
        }
