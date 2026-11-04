"""安全更新的错误路径：用假的命令执行器（不跑真 git / pip），逐条检查每一步没过时
返回什么、记下什么、数据库动没动。真实 git 仓库里的正常流程见 test_safe_update.py。"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from roundtable.core import safe_update as su
from roundtable.core.storage import connect

HEAD, NEW, UP = "a" * 40, "b" * 40, "c" * 40


class FakeRun:
    """按命令前缀返回结果。rules: [(前缀元组, 返回码 | 异常, 输出)]，先匹配先用；
    同一前缀出现多次时按顺序依次用（最后一个一直用）。"""

    def __init__(self, rules):
        self.rules = rules
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        self.kwargs.append(kw)
        matches = [r for r in self.rules if tuple(argv[: len(r[0])]) == r[0]]
        if not matches:
            return subprocess.CompletedProcess(argv, 0, "", "")
        rule = matches[0]
        if len(matches) > 1:
            self.rules.remove(rule)
        _, rc, out = rule
        if isinstance(rc, BaseException):
            raise rc
        return subprocess.CompletedProcess(argv, rc, out if rc == 0 else "", "" if rc == 0 else out)

    def ran(self, *prefix: str) -> int:
        return sum(1 for c in self.calls if tuple(c[: len(prefix)]) == prefix)


def make(tmp_path, rules, *, with_db=True, **kw):
    data = tmp_path / "data"
    if with_db:
        connect(data / "roundtable.db").close()
    said: list[str] = []
    run = FakeRun(list(rules))
    ticks = iter(range(10_000))
    upd = su.SafeUpdate(
        tmp_path,
        data,
        run=run,
        python="py",
        now=lambda: datetime(2026, 11, 4, 9, 0, next(ticks) % 60),
        say=said.append,
        **kw,
    )
    return upd, run, said


def git_ok(*extra):
    """一次正常的「有新版本」流程：HEAD → fetch → upstream → pull → 新 HEAD → diff。"""
    return [
        (("git", "rev-parse", "HEAD"), 0, HEAD),
        (("git", "rev-parse", "HEAD"), 0, NEW),
        (("git", "rev-parse", "@{u}"), 0, UP),
        *extra,
    ]


# --- 小工具 -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        ("data/roundtable.db", True),
        ("./data/x.json", True),
        ("data\\win.txt", True),
        ("sub/.env", True),
        (".env.example", False),
        ("venv/lib/x.py", True),
        ("src/x.db-wal", True),
        ("src/roundtable/core/data.py", False),
        ("docs/database.md", False),
        ("mydata/file", False),
    ],
)
def test_protected(path, bad):
    assert su.protected(path) is bad


def test_db_version_missing_corrupt_and_without_table(tmp_path):
    assert su.db_version(tmp_path / "none.db") is None
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database at all" * 100)
    assert su.db_version(junk) is None
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    assert su.db_version(empty) is None  # 没有 schema_version 表
    real = tmp_path / "real.db"
    connect(real).close()
    assert su.db_version(real) and su.db_version(real) > 0


def test_db_version_does_not_create_or_modify_file(tmp_path):
    db = tmp_path / "real.db"
    connect(db).close()
    before = db.read_bytes()
    su.db_version(db)
    assert db.read_bytes() == before


def test_state_tolerates_missing_corrupt_and_non_dict(tmp_path):
    upd, _, _ = make(tmp_path, [], with_db=False)
    assert upd.state() == {}
    upd.data.mkdir(parents=True, exist_ok=True)
    upd.state_path.write_text("{oops", encoding="utf-8")
    assert upd.state() == {}
    upd.state_path.write_text("[1, 2]", encoding="utf-8")
    assert upd.state() == {}
    upd.save(phase="ok", 说明="中文")
    assert upd.state() == {"phase": "ok", "说明": "中文"}
    assert not upd.state_path.with_suffix(".part").exists()


def test_sh_reports_failures_in_plain_words(tmp_path):
    upd, run, _ = make(
        tmp_path,
        [
            (("boom",), OSError("no such file"), ""),
            (("slow",), subprocess.TimeoutExpired("slow", 1), ""),
            (("bad", "cmd"), 2, "x" * 1000 + "最后一行"),
        ],
        with_db=False,
    )
    with pytest.raises(su.UpdateError, match="boom 没能运行"):
        upd.sh("boom")
    with pytest.raises(su.UpdateError, match="slow 没能运行"):
        upd.sh("slow")
    with pytest.raises(su.UpdateError) as err:
        upd.sh("bad", "cmd", "arg", "more")
    msg = str(err.value)
    assert msg.startswith("bad cmd arg 失败：") and msg.endswith("最后一行")
    assert len(msg) < 450  # 只留输出末尾
    assert run.kwargs[-1]["cwd"] == str(tmp_path) and run.kwargs[-1]["check"] is False


def test_pip_spec_with_and_without_extras(tmp_path):
    upd, run, said = make(tmp_path, [], with_db=False)
    upd.pip("dev")
    upd.pip("")
    assert run.calls == [
        ["py", "-m", "pip", "install", "-e", ".[dev]"],
        ["py", "-m", "pip", "install", "-e", "."],
    ]
    assert run.kwargs[0]["timeout"] == 1800
    assert said == ["pip install -e .[dev] …", "pip install -e . …"]


def test_smoke_runs_on_a_copy_and_never_touches_real_db(tmp_path):
    upd, run, _ = make(tmp_path, [])
    before = upd.db.read_bytes()
    seen = {}

    def spy(argv, **kw):
        copy = Path(kw["env"]["ROUNDTABLE_DB_PATH"])
        seen["copy_exists"] = copy.is_file()
        seen["copy_version"] = su.db_version(copy)
        seen["argv"] = argv
        seen["env"] = kw["env"]
        # 新代码在副本上做迁移：写副本不影响正在用的库
        sqlite3.connect(copy).execute("CREATE TABLE smoke_marker (x)").connection.commit()
        return subprocess.CompletedProcess(argv, 0, "", "")

    upd.run_ = spy
    upd.smoke()
    assert seen["copy_exists"] and seen["copy_version"] == su.db_version(upd.db)
    assert seen["argv"][:2] == ["py", str(tmp_path / "scripts" / "safe_update.py")]
    assert seen["argv"][2:4] == ["smoke", "--db"]
    assert seen["env"]["PYTHONIOENCODING"] == "utf-8"
    assert upd.db.read_bytes() == before
    assert not Path(seen["env"]["ROUNDTABLE_DB_PATH"]).exists()  # 临时目录已清掉


def test_smoke_without_database_still_runs(tmp_path):
    upd, run, _ = make(tmp_path, [], with_db=False)
    upd.smoke()
    assert run.ran("py") == 1


def test_checkpoint_without_db_is_none_and_failure_leaves_no_part_file(tmp_path, monkeypatch):
    upd, _, _ = make(tmp_path, [], with_db=False)
    assert upd.checkpoint("update") is None

    upd, _, _ = make(tmp_path / "x", [])

    def broken(src, dst):
        Path(dst).write_bytes(b"half")
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(su, "copy_db", broken)
    with pytest.raises(su.UpdateError, match="存档点没存成"):
        upd.checkpoint("update")
    folder = upd.db.parent / su.BACKUP_DIR
    assert list(folder.iterdir()) == []


def test_checkpoint_pruning_ignores_foreign_files(tmp_path):
    upd, _, _ = make(tmp_path, [], keep_checkpoints=2)
    folder = upd.db.parent / su.BACKUP_DIR
    folder.mkdir(parents=True)
    (folder / "notes.txt").write_text("别删我")
    (folder / "roundtable-auto-20260101.db").write_bytes(b"")
    names = [upd.checkpoint("update") for _ in range(4)]
    left = sorted(p.name for p in folder.iterdir())
    assert names[-2:] == [n for n in left if n.startswith("roundtable-checkpoint")]
    assert "notes.txt" in left and "roundtable-auto-20260101.db" in left


# --- update 的分支 -----------------------------------------------------------------


def test_not_a_git_repo_only_installs(tmp_path):
    upd, run, said = make(tmp_path, [(("git", "rev-parse", "HEAD"), 128, "not a git repo")])
    assert upd.update() == su.OK
    assert run.ran("py", "-m", "pip") == 1 and run.ran("git", "fetch") == 0
    assert "不是 git 仓库" in said[0]


def test_not_a_git_repo_and_pip_fails(tmp_path):
    upd, _, said = make(
        tmp_path,
        [(("git", "rev-parse", "HEAD"), 128, "nope"), (("py",), 1, "resolver error")],
    )
    assert upd.update() == su.FAILED
    assert any(s.startswith("[error]") for s in said)


@pytest.mark.parametrize(("strict", "code"), [(False, su.NOTHING), (True, su.FAILED)])
def test_fetch_failure(tmp_path, strict, code):
    upd, run, _ = make(
        tmp_path, [(("git", "rev-parse", "HEAD"), 0, HEAD), (("git", "fetch"), 1, "offline")]
    )
    assert upd.update(strict=strict) == code
    assert run.ran("py") == (0 if strict else 1)
    assert upd.state() == {}  # 没开始更新，不写记录


def test_skips_the_version_you_undid(tmp_path):
    upd, run, said = make(
        tmp_path,
        [(("git", "rev-parse", "HEAD"), 0, HEAD), (("git", "rev-parse", "@{u}"), 0, UP)],
    )
    upd.save(phase="undone", skipped=UP)
    assert upd.update() == su.OK
    assert run.ran("git", "pull") == 0
    assert "撤销过" in said[0]
    assert list((upd.db.parent).glob(su.BACKUP_DIR + "/*")) == []  # 也没存存档点


def test_newer_version_than_the_one_you_undid_is_installed(tmp_path):
    upd, run, _ = make(tmp_path, git_ok())
    upd.save(phase="undone", skipped="d" * 40)
    assert upd.update() == su.OK
    assert run.ran("git", "pull") == 1
    assert upd.state()["phase"] == "ok" and upd.state()["to_commit"] == NEW


@pytest.mark.parametrize(("strict", "code"), [(False, su.NOTHING), (True, su.FAILED)])
def test_checkpoint_failure_blocks_update(tmp_path, monkeypatch, strict, code):
    upd, run, said = make(tmp_path, git_ok())

    def boom(label):
        raise su.UpdateError("存档点没存成：磁盘满了")

    monkeypatch.setattr(upd, "checkpoint", boom)
    assert upd.update(strict=strict) == code
    assert run.ran("git", "pull") == 0
    state = upd.state()
    assert state["phase"] == "failed" and "磁盘满了" in state["reason"]
    assert any("为保护数据" in s for s in said)


@pytest.mark.parametrize(("strict", "code"), [(False, su.NOTHING), (True, su.FAILED)])
def test_pull_failure_records_and_uses_local_code(tmp_path, strict, code):
    upd, run, _ = make(tmp_path, git_ok((("git", "pull"), 1, "conflict")))
    assert upd.update(strict=strict) == code
    state = upd.state()
    assert state["phase"] == "failed" and "conflict" in state["reason"]
    assert state["from_commit"] == HEAD and state["checkpoint"]
    assert run.ran("git", "reset") == 0  # 没拉成，不用退回


def test_ff_only_flag_is_passed_to_pull(tmp_path):
    upd, run, _ = make(tmp_path, git_ok())
    upd.update(ff_only=True)
    pull = next(c for c in run.calls if c[:2] == ["git", "pull"])
    assert pull == ["git", "pull", "--ff-only", "--autostash"]


def test_rollback_that_also_fails_is_reported_as_failed(tmp_path):
    upd, run, said = make(
        tmp_path,
        git_ok(
            (("git", "diff"), 0, "data/roundtable.db\nsrc/x.py"),
            (("git", "reset"), 1, "local changes would be overwritten"),
        ),
    )
    assert upd.update() == su.FAILED
    state = upd.state()
    assert state["phase"] == "failed"
    assert "不该改的地方" in state["reason"] and "退回也没成功" in state["reason"]
    assert state["to_commit"] == NEW
    assert any("退回没成功" in s for s in said)
    assert run.ran("py") == 0  # 白名单没过：没装依赖、没跑冒烟


def test_protected_list_in_reason_is_capped(tmp_path):
    files = "\n".join(f"data/f{i}.json" for i in range(9))
    upd, _, _ = make(tmp_path, git_ok((("git", "diff"), 0, files)))
    assert upd.update() == su.ROLLED_BACK
    reason = upd.state()["reason"]
    assert "data/f4.json" in reason and "data/f5.json" not in reason


# --- undo 的分支 -------------------------------------------------------------------


def _bump(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO schema_version VALUES (99999, 'next', 'x')")
    conn.commit()
    conn.close()


def test_undo_refuses_same_commit(tmp_path):
    upd, run, said = make(tmp_path, [])
    upd.save(phase="ok", from_commit=HEAD, to_commit=HEAD)
    assert upd.undo() == su.FAILED and run.calls == []


def test_undo_refuses_when_db_upgraded_without_checkpoint(tmp_path):
    upd, run, said = make(tmp_path, [])
    upd.save(phase="ok", from_commit=HEAD, to_commit=NEW, db_version=1, checkpoint=None)
    _bump(upd.db)
    assert upd.undo() == su.FAILED
    assert run.calls == []  # 代码也不退回
    assert "没有更新前的存档点" in said[-1]


def test_undo_code_reset_failure(tmp_path):
    upd, _, said = make(tmp_path, [(("git", "reset"), 1, "conflict")])
    upd.save(phase="ok", from_commit=HEAD, to_commit=NEW, db_version=None)
    assert upd.undo() == su.FAILED
    assert upd.state()["phase"] == "ok"  # 记录不变，之后还能再试
    assert "代码没退回" in said[-1]


def test_undo_db_restore_failure_is_reported(tmp_path):
    upd, _, said = make(tmp_path, [])
    version = su.db_version(upd.db)
    upd.save(phase="ok", from_commit=HEAD, to_commit=NEW, db_version=version, checkpoint="gone.db")
    _bump(upd.db)
    assert upd.undo() == su.FAILED
    assert "数据库没恢复" in said[-1] and "gone.db" in said[-1]
    assert su.db_version(upd.db) == 99999  # 正在用的库没被半截覆盖


def test_undo_when_db_version_unknown_does_not_restore(tmp_path):
    upd, run, _ = make(tmp_path, [])
    upd.save(phase="ok", from_commit=HEAD, to_commit=NEW, db_version=None, checkpoint="x.db")
    _bump(upd.db)
    assert upd.undo() == su.OK
    state = upd.state()
    assert state["phase"] == "undone" and state["restored"] is None and state["skipped"] == NEW
    assert run.calls == [["git", "reset", "--keep", HEAD]]


def test_state_file_written_as_utf8_json(tmp_path):
    upd, _, _ = make(tmp_path, [])
    upd.save(phase="failed", reason="磁盘满了")
    raw = upd.state_path.read_bytes().decode("utf-8")
    assert "磁盘满了" in raw and json.loads(raw)["phase"] == "failed"


def test_module_imports_only_stdlib_at_top_level():
    # 第一次运行时 .venv 里还什么都没装：模块层只能用标准库
    import ast

    tree = ast.parse(Path(su.__file__).read_text(encoding="utf-8"))
    top = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            top.add(node.module.split(".")[0])
    top.discard("__future__")
    assert top <= set(sys.stdlib_module_names), top - set(sys.stdlib_module_names)
