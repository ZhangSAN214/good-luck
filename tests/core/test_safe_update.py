"""安全更新：在真实的 git 仓库里模拟远端更新。好的更新装上；改到白名单外、pip 或冒烟没过就退回；
存档点存不成就不更新；撤销会把代码和（升级过的）数据库一起退回，之后不再自动装撤销的那一版。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from roundtable.core import safe_update as su
from roundtable.core.storage import connect
from roundtable.core.storage.migrations import MIGRATIONS


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def commit(repo: Path, name: str, text: str = "x", *, force: bool = False) -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    git(repo, "add", *(["-f"] if force else []), name)
    git(repo, "commit", "-qm", f"change {name}")
    git(repo, "push", "-q")
    return git(repo, "rev-parse", "HEAD")


class Box(su.SafeUpdate):
    """pip 和冒烟测试换成假的（记录调用；可以指定让它失败）。"""

    def __init__(self, *a, smoke_ok=True, pip_ok=True, **kw):
        super().__init__(*a, **kw)
        self.smoke_ok, self.pip_ok = smoke_ok, pip_ok
        self.pips = self.smokes = 0
        self.said: list[str] = []
        self.say = self.said.append

    def pip(self, extras):
        self.pips += 1
        if not self.pip_ok:
            raise su.UpdateError("pip install 失败：依赖装不上")

    def smoke(self):
        self.smokes += 1
        if not self.smoke_ok:
            raise su.UpdateError("冒烟测试没过：GET / 返回 500")


@pytest.fixture
def repos(tmp_path):
    """remote（裸仓库）、dev（推更新的人）、pc（用户电脑上的项目目录，带 data/）。"""
    remote, dev, pc = tmp_path / "remote.git", tmp_path / "dev", tmp_path / "pc"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(dev)], check=True)
    for r in (dev,):
        git(r, "config", "user.email", "t@example.com")
        git(r, "config", "user.name", "t")
    (dev / ".gitignore").write_text("data/\n.env\n", encoding="utf-8")
    git(dev, "add", ".gitignore")
    git(dev, "commit", "-qm", "init")
    git(dev, "push", "-q", "-u", "origin", "HEAD")
    subprocess.run(["git", "clone", "-q", str(remote), str(pc)], check=True)
    git(pc, "config", "user.email", "t@example.com")
    git(pc, "config", "user.name", "t")
    connect(pc / "data" / "roundtable.db").close()
    return dev, pc


def box(pc: Path, **kw) -> Box:
    ticks = iter(range(10_000))
    return Box(pc, pc / "data", now=lambda: datetime(2026, 11, 2, 9, 0, next(ticks) % 60), **kw)


def points(pc: Path) -> list[str]:
    folder = pc / "data" / "db_backups"
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def test_protected_paths():
    for p in ("data/roundtable.db", ".env", "config/.env", ".venv/x", "a/b.db", "./data/x"):
        assert su.protected(p), p
    for p in ("src/roundtable/cli.py", "start.bat", "docs/data.md", ".env.example"):
        assert not su.protected(p), p


def test_already_up_to_date(repos):
    _, pc = repos
    b = box(pc)
    assert b.update() == su.OK
    assert b.pips == 1 and b.smokes == 0 and points(pc) == []
    assert not (pc / "data" / su.STATE_FILE).exists()


def test_good_update_is_installed(repos):
    dev, pc = repos
    old = git(pc, "rev-parse", "HEAD")
    new = commit(dev, "src/app.py", "print(1)")
    b = box(pc)
    assert b.update(ff_only=True) == su.OK
    assert git(pc, "rev-parse", "HEAD") == new and b.smokes == 1
    state = b.state()
    assert state["phase"] == "ok" and state["from_commit"] == old and state["to_commit"] == new
    assert state["checkpoint"].endswith("-update.db") and points(pc) == [state["checkpoint"]]


def test_update_touching_protected_paths_is_rolled_back(repos):
    dev, pc = repos
    old = git(pc, "rev-parse", "HEAD")
    commit(dev, "data/evil.txt", "x", force=True)
    b = box(pc)
    assert b.update() == su.ROLLED_BACK
    assert git(pc, "rev-parse", "HEAD") == old
    state = b.state()
    assert state["phase"] == "rolled_back" and "data/evil.txt" in state["reason"]
    assert b.smokes == 0


def test_failed_smoke_rolls_back_and_keeps_database(repos):
    dev, pc = repos
    old = git(pc, "rev-parse", "HEAD")
    commit(dev, "src/app.py", "broken")
    db = pc / "data" / "roundtable.db"
    before = db.read_bytes()
    b = box(pc, smoke_ok=False)
    assert b.update() == su.ROLLED_BACK
    assert git(pc, "rev-parse", "HEAD") == old
    assert b.pips == 2  # 新代码装一次，退回后旧代码再装一次
    assert db.read_bytes() == before
    assert "GET / 返回 500" in b.state()["reason"]


def test_failed_pip_rolls_back(repos):
    dev, pc = repos
    old = git(pc, "rev-parse", "HEAD")
    commit(dev, "pyproject.toml", "bad")
    b = box(pc, pip_ok=False)
    assert b.update() == su.FAILED  # 退回后旧代码也装不上
    assert git(pc, "rev-parse", "HEAD") == old


def test_no_checkpoint_no_update(repos):
    dev, pc = repos
    old = git(pc, "rev-parse", "HEAD")
    commit(dev, "src/app.py")
    (pc / "data" / "db_backups").write_text("占位", encoding="utf-8")
    b = box(pc)
    assert b.update() == su.NOTHING
    assert git(pc, "rev-parse", "HEAD") == old
    assert b.update(strict=True) == su.FAILED
    assert b.state()["phase"] == "failed"


def test_unreachable_remote_uses_local_code(repos, tmp_path):
    _, pc = repos
    git(pc, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    b = box(pc)
    assert b.update() == su.NOTHING and b.pips == 1
    assert b.update(strict=True) == su.FAILED


def _bump_db_version(db: Path) -> None:
    raw = sqlite3.connect(db)
    raw.execute("INSERT INTO schema_version VALUES (?, 'next', 'x')", (MIGRATIONS[-1][0] + 1,))
    raw.execute("CREATE TABLE added_by_new_code (x)")
    raw.commit()
    raw.close()


def test_undo_restores_code_and_upgraded_database(repos):
    dev, pc = repos
    old = git(pc, "rev-parse", "HEAD")
    new = commit(dev, "src/app.py", "v2")
    b = box(pc)
    assert b.update() == su.OK
    db = pc / "data" / "roundtable.db"
    _bump_db_version(db)  # 新代码启动时升级了数据库
    assert b.undo() == su.OK
    assert git(pc, "rev-parse", "HEAD") == old
    assert su.db_version(db) == MIGRATIONS[-1][0]
    state = b.state()
    assert state["phase"] == "undone" and state["skipped"] == new
    assert any(n.endswith("-撤销前.db") for n in points(pc))
    # 撤销的那一版不再自动装；有更新的版本才更新
    b2 = box(pc)
    assert b2.update() == su.OK and git(pc, "rev-parse", "HEAD") == old
    assert any("撤销过" in s for s in b2.said)
    newer = commit(dev, "src/app.py", "v3")
    assert box(pc).update() == su.OK and git(pc, "rev-parse", "HEAD") == newer


def test_undo_without_a_passed_update(repos):
    _, pc = repos
    assert box(pc).undo() == su.FAILED
    (pc / "data" / su.STATE_FILE).write_text(json.dumps({"phase": "rolled_back"}), "utf-8")
    assert box(pc).undo() == su.FAILED


def test_undo_keeps_database_when_schema_did_not_change(repos):
    dev, pc = repos
    commit(dev, "src/app.py", "v2")
    b = box(pc)
    assert b.update() == su.OK
    db = pc / "data" / "roundtable.db"
    raw = sqlite3.connect(db)
    raw.execute("CREATE TABLE written_after_update (x)")
    raw.commit()
    raw.close()
    assert b.undo() == su.OK
    raw = sqlite3.connect(db)
    names = {r[0] for r in raw.execute("SELECT name FROM sqlite_master")}
    raw.close()
    assert "written_after_update" in names  # 版本没变：更新后写的数据保留


def test_checkpoints_are_pruned(repos):
    _, pc = repos
    b = box(pc, keep_checkpoints=3)
    for _ in range(5):
        b.checkpoint("update")
    assert len(points(pc)) == 3


def test_real_smoke_on_a_copy(tmp_path):
    db = tmp_path / "rt.db"
    connect(db).close()
    assert su.run_smoke(db) == []
    assert su.smoke_main(["--db", str(db)]) == 0
    assert su.smoke_main([]) == 2
