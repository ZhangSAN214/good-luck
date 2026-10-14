from __future__ import annotations

import os
from pathlib import Path

import pytest

from roundtable.core.attachments import FileStore
from roundtable.core.config import load_config
from roundtable.core.storage import Repository, connect
from roundtable.core.tools import Workspace, clean_path

ROOT = Path(__file__).resolve().parents[3]
RULES = load_config().roundtable.tools.files


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("plot.png", "plot.png"),
        ("out/figs/a.png", "figs/a.png"),
        ("..\\..\\.env", None),
        ("../x.txt", None),
        ("/etc/passwd", None),
        ("a/../../b.txt", None),
        (".hidden", None),
        ("con<>.txt", None),
        ("", None),
        ("a/b/c/d/e.txt", None),
    ],
)
def test_clean_path(raw, clean):
    assert clean_path(raw) == clean


def workspace(repo=None, **kw):
    repo = repo or Repository(connect(":memory:"))
    sid = kw.pop("sid", None) or repo.create_session("q", seed=1)
    store = kw.pop("store", None) or FileStore(None)
    return (
        Workspace(
            session_id=sid,
            table_no=0,
            code="甲",
            repo=repo,
            store=store,
            rules=kw.pop("rules", RULES),
            project_root=ROOT,
        ),
        repo,
        sid,
        store,
    )


def test_workspace_is_outside_project_and_collects_whitelisted_files():
    ws, repo, sid, _ = workspace()
    try:
        assert ROOT not in ws.root.parents
        (ws.out / "a.md").write_text("# hi")
        (ws.out / "run.exe").write_bytes(b"MZ")
        (ws.out / "sub").mkdir()
        (ws.out / "sub" / "t.csv").write_text("a,b")
        got = ws.collect(step="answer", tool_call_id=None, step_count=0)
        assert sorted(r["path"] for r in got.new) == ["a.md", "sub/t.csv"]
        assert any("run.exe" in r and ".exe" in r for r in got.rejected)
        assert not (ws.out / "run.exe").exists()
        again = ws.collect(step="answer", tool_call_id=None, step_count=2)
        assert again.new == []  # 没变的文件不重复登记
        (ws.out / "a.md").write_text("# changed")
        assert [
            r["path"] for r in ws.collect(step="revise", tool_call_id=None, step_count=0).new
        ] == ["a.md"]
        assert len(repo.files(sid)) == 3
    finally:
        ws.close()


@pytest.mark.skipif(os.name == "nt", reason="符号链接需要管理员权限")
def test_symlinks_and_hardlinks_rejected(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("KEY")
    ws, repo, sid, _ = workspace()
    try:
        (ws.out / "link.txt").symlink_to(secret)
        (ws.out / "dir").symlink_to(tmp_path, target_is_directory=True)
        os.link(secret, ws.out / "hard.txt")
        got = ws.collect(step="answer", tool_call_id=None, step_count=0)
        assert got.new == []
        assert any("符号链接" in r for r in got.rejected) and any(
            "硬链接" in r for r in got.rejected
        )
        assert secret.read_text() == "KEY"  # 原文件不受影响
        assert repo.files(sid) == []
    finally:
        ws.close()


def test_size_and_count_limits():
    rules = RULES.model_copy(update={"max_file_mb": 0.001, "max_files_per_step": 2})
    ws, *_ = workspace(rules=rules)
    try:
        (ws.out / "big.txt").write_text("x" * 5000)
        for i in range(3):
            (ws.out / f"s{i}.txt").write_text("ok")
        got = ws.collect(step="answer", tool_call_id=None, step_count=0)
        assert len(got.new) == 2
        assert any("MB" in r for r in got.rejected) and any("最多生成" in r for r in got.rejected)
    finally:
        ws.close()


def test_files_restored_and_attachments_in_inputs():
    from roundtable.core.attachments import Attachment

    repo = Repository(connect(":memory:"))
    store = FileStore(None)
    ws, _, sid, _ = workspace(repo=repo, store=store)
    (ws.out / "keep.py").write_text("print(1)")
    ws.collect(step="answer", tool_call_id=None, step_count=0)
    ws.close()
    key = store.save(b"col\n1\n", "csv")
    att = Attachment("a1", "../../data.csv", "text", "text/csv", "csv", 6, key, "ready")
    again = Workspace(
        session_id=sid,
        table_no=0,
        code="甲",
        repo=repo,
        store=store,
        rules=RULES,
        attachments=(att,),
    )
    try:
        assert (again.out / "keep.py").read_text() == "print(1)"
        assert (again.root / "in" / "data.csv").read_bytes() == b"col\n1\n"
        assert again.collect(step="revise", tool_call_id=None, step_count=0).new == []
    finally:
        again.close()
