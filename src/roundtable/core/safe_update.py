"""安全更新（docs/SAFE_UPDATE.md）：电脑上更新圆桌时先备份、只许改白名单、冒烟测试不过就原样退回。

``scripts/safe_update.py update`` 由 start.bat / update_and_start.bat 调用，步骤：

1. 记下现在的代码版本（git commit）和数据库版本，存一个「更新前」存档点（标签 ``update``）；
   **存档点没存成就不更新**。
2. ``git fetch`` → 有新版本才 ``git pull``；你撤销过的那一版不再自动装。
3. 白名单：这次更新改到的文件不许落在 ``data/``、``.env``、``.venv/`` 或 ``*.db`` 上，碰了就退回。
4. ``pip install -e``。
5. 冒烟测试：把数据库复制一份到临时目录，用新代码在副本上做迁移、建服务、打开首页和几个接口。
   正在用的数据库在这一步完全不动。
6. 任何一步没过：``git reset --keep`` 回到更新前的版本、重新 ``pip install``，记下原因；
   数据库没动过，所以不用恢复。
7. 结果写到 ``data/update_state.json``，「出问题点这里」会显示。

``undo``（undo_update.bat）撤销上一次通过的更新：代码退回更新前；数据库版本变了就把「更新前」
存档点写回（之前先存「撤销前」存档点，反悔还能恢复），并记住这一版，之后不再自动装它。

本模块在模块层只用标准库：第一次运行时 .venv 里还什么都没装，也要能存档点和拉代码。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

STATE_FILE = "update_state.json"
BACKUP_DIR = "db_backups"  # 和 core/runtime.py 的 BACKUP_DIR、core/backup.py 的文件名格式一致
UPDATE_LABEL = "update"
UNDO_LABEL = "撤销前"
KEEP_CHECKPOINTS = 10
_POINT = re.compile(r"^roundtable-checkpoint-\d{8}-\d{6}-[\w-]{1,24}\.db$")

# 更新不许碰的地方（这些都不该进 git；真被改到说明这次更新有问题）
PROTECTED_DIRS = ("data/", ".venv/", "venv/")
PROTECTED_FILES = (".env",)
PROTECTED_SUFFIXES = (".db", ".db-wal", ".db-shm")

OK, FAILED, ROLLED_BACK, NOTHING = 0, 1, 2, 3
SMOKE_TIMEOUT_S = 300

Run = Callable[..., subprocess.CompletedProcess[str]]


class UpdateError(RuntimeError):
    """更新中某一步没过（信息是给用户看的大白话）。"""


def protected(path: str) -> bool:
    p = path.replace("\\", "/").removeprefix("./")
    name = p.rsplit("/", 1)[-1]
    return (
        any(p.startswith(d) for d in PROTECTED_DIRS)
        or name in PROTECTED_FILES
        or p.endswith(PROTECTED_SUFFIXES)
    )


def db_version(db: Path) -> int | None:
    """只读打开，读数据库版本；没有文件或读不了返回 None。"""
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0] or 0)
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def copy_db(src: Path, dst: Path) -> None:
    """用 SQLite 在线备份接口复制（服务开着也不会复制出半截文件）。"""
    a = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    b = sqlite3.connect(str(dst))
    try:
        a.backup(b)
    finally:
        b.close()
        a.close()


class SafeUpdate:
    def __init__(
        self,
        root: Path,
        data: Path,
        *,
        db: Path | None = None,
        run: Run = subprocess.run,
        python: str = sys.executable,
        now: Callable[[], datetime] = datetime.now,
        say: Callable[[str], None] = print,
        keep_checkpoints: int = KEEP_CHECKPOINTS,
    ) -> None:
        self.root = root
        self.data = data
        self.db = db if db is not None else data / "roundtable.db"
        self.run_ = run
        self.python = python
        self.now = now
        self.say = say
        self.keep = keep_checkpoints

    # ---- 记录 ----

    @property
    def state_path(self) -> Path:
        return self.data / STATE_FILE

    def state(self) -> dict[str, Any]:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def save(self, **fields: Any) -> dict[str, Any]:
        self.data.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".part")
        tmp.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)
        return fields

    def stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    # ---- 外部命令 ----

    def sh(self, *argv: str, timeout: float = 600, env: dict[str, str] | None = None) -> str:
        try:
            out = self.run_(
                list(argv),
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise UpdateError(f"{argv[0]} 没能运行：{exc}") from None
        if out.returncode != 0:
            tail = (out.stderr or out.stdout or "").strip()[-400:]
            raise UpdateError(f"{' '.join(argv[:3])} 失败：{tail}")
        return (out.stdout or "").strip()

    def git(self, *args: str) -> str:
        return self.sh("git", *args)

    def pip(self, extras: str) -> None:
        spec = f".[{extras}]" if extras else "."
        self.say(f"pip install -e {spec} …")
        self.sh(self.python, "-m", "pip", "install", "-e", spec, timeout=1800)

    # ---- 存档点（只用标准库，格式和 core/backup.py 一致）----

    def checkpoint(self, label: str) -> str | None:
        """存一个存档点，返回文件名；还没有数据库时返回 None。存不成抛 UpdateError。"""
        if not self.db.is_file():
            return None
        folder = self.db.parent / BACKUP_DIR
        name = f"roundtable-checkpoint-{self.now():%Y%m%d-%H%M%S}-{label}.db"
        final = folder / name
        tmp = final.with_name(name + ".part")
        try:
            folder.mkdir(parents=True, exist_ok=True)
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()
            copy_db(self.db, tmp)
            os.replace(tmp, final)
        except (OSError, sqlite3.Error) as exc:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise UpdateError(f"存档点没存成：{exc}") from None
        points = sorted(p.name for p in folder.iterdir() if _POINT.match(p.name))
        for old in points[: max(0, len(points) - self.keep)]:
            with contextlib.suppress(OSError):
                (folder / old).unlink()
        return name

    # ---- 冒烟测试 ----

    def smoke(self) -> None:
        """在数据库副本上跑新代码（另起一个进程，用的是刚拉下来的代码）。"""
        with tempfile.TemporaryDirectory(prefix="roundtable-smoke-") as tmp:
            copy = Path(tmp) / "roundtable.db"
            if self.db.is_file():
                copy_db(self.db, copy)
            env = dict(os.environ)
            env["ROUNDTABLE_DB_PATH"] = str(copy)
            env["PYTHONIOENCODING"] = "utf-8"
            script = str(self.root / "scripts" / "safe_update.py")
            self.say("冒烟测试（在数据库副本上）…")
            self.sh(
                self.python, script, "smoke", "--db", str(copy), timeout=SMOKE_TIMEOUT_S, env=env
            )

    # ---- 更新 ----

    def update(self, extras: str = "dev", *, ff_only: bool = False, strict: bool = False) -> int:
        """返回 OK（已更新或已是最新）、ROLLED_BACK（没过、已退回）、
        NOTHING（没更新成，照旧用本地代码）、FAILED（本地代码也装不上）。"""
        last = self.state()
        try:
            head = self.git("rev-parse", "HEAD")
        except UpdateError as exc:
            self.say(f"[warn] 不是 git 仓库或 git 不可用，跳过更新：{exc}")
            return self._install_only(extras, strict)
        version = db_version(self.db)
        try:
            self.git("fetch", "--quiet")
            upstream = self.git("rev-parse", "@{u}")
        except UpdateError as exc:
            self.say(f"[warn] 拿不到远端的新版本，用本地代码：{exc}")
            return FAILED if strict else self._install_only(extras, False, NOTHING)
        if upstream == head:
            self.say("已经是最新版本。")
            return self._install_only(extras, strict)
        if last.get("phase") == "undone" and last.get("skipped") == upstream:
            self.say("你撤销过远端这一版，等有更新的版本再装。")
            return self._install_only(extras, strict)
        try:
            point = self.checkpoint(UPDATE_LABEL)
        except UpdateError as exc:
            self.say(f"[warn] {exc}；为保护数据，这次不更新。")
            self.save(phase="failed", reason=str(exc), started_at=self.stamp(), from_commit=head)
            return FAILED if strict else self._install_only(extras, False, NOTHING)
        record = {
            "from_commit": head,
            "db_version": version,
            "checkpoint": point,
            "started_at": self.stamp(),
        }
        self.save(phase="begun", **record)
        try:
            self.git("pull", "--ff-only" if ff_only else "--no-rebase", "--autostash")
        except UpdateError as exc:
            self.save(phase="failed", reason=str(exc), finished_at=self.stamp(), **record)
            self.say(f"[warn] 拉代码没成功，用本地代码：{exc}")
            return FAILED if strict else self._install_only(extras, False, NOTHING)
        new = self.git("rev-parse", "HEAD")
        try:
            changed = self.git("diff", "--name-only", head, new).splitlines()
            bad = [p for p in changed if protected(p)]
            if bad:
                raise UpdateError(f"这次更新改到了不该改的地方：{'、'.join(bad[:5])}")
            self.pip(extras)
            self.smoke()
        except UpdateError as exc:
            return self._rollback(head, new, extras, str(exc), record)
        self.save(phase="ok", to_commit=new, finished_at=self.stamp(), **record)
        self.say(f"更新完成并通过冒烟测试：{head[:7]} → {new[:7]}")
        return OK

    def _install_only(self, extras: str, strict: bool, code: int = OK) -> int:
        try:
            self.pip(extras)
        except UpdateError as exc:
            self.say(f"[error] {exc}")
            return FAILED
        return code

    def _rollback(self, head: str, new: str, extras: str, reason: str, record: dict) -> int:
        self.say(f"[warn] 更新没通过：{reason}")
        self.say(f"正在退回到更新前的版本 {head[:7]} …")
        try:
            self.git("reset", "--keep", head)
            self.pip(extras)
        except UpdateError as exc:
            self.save(
                phase="failed",
                reason=f"{reason}；退回也没成功：{exc}",
                to_commit=new,
                finished_at=self.stamp(),
                **record,
            )
            self.say(f"[error] 退回没成功：{exc}")
            return FAILED
        self.save(
            phase="rolled_back", reason=reason, to_commit=new, finished_at=self.stamp(), **record
        )
        self.say("已退回，数据库没动过。")
        return ROLLED_BACK

    # ---- 撤销上一次更新 ----

    def undo(self) -> int:
        last = self.state()
        old, new = last.get("from_commit"), last.get("to_commit")
        if last.get("phase") != "ok" or not old or not new or old == new:
            self.say("没有可以撤销的更新（只有通过冒烟测试的更新才能撤销）。")
            return FAILED
        point = last.get("checkpoint")
        before = last.get("db_version")
        restore = before is not None and (db_version(self.db) or 0) > int(before)
        if restore and not point:
            self.say("数据库升级过，但没有更新前的存档点，不能安全撤销。")
            return FAILED
        try:
            self.git("reset", "--keep", old)
        except UpdateError as exc:
            self.say(f"代码没退回（你改过的文件和这次更新冲突？）：{exc}")
            return FAILED
        self.say(f"代码已退回到 {old[:7]}。")
        saved = None
        if restore:
            try:
                saved = self.checkpoint(UNDO_LABEL)
                folder = self.db.parent / BACKUP_DIR
                copy_db(folder / str(point), self.db)
            except (UpdateError, OSError, sqlite3.Error) as exc:
                self.say(f"[error] 数据库没恢复：{exc}。可以在「备份」里手动恢复 {point}")
                return FAILED
            self.say(f"数据库已恢复到更新前（{point}）；撤销前的数据存在 {saved}，反悔可以恢复它。")
        self.save(
            phase="undone",
            from_commit=old,
            to_commit=new,
            skipped=new,
            restored=point if restore else None,
            saved=saved,
            finished_at=self.stamp(),
        )
        return OK
