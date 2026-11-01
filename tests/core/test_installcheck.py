"""装后核对：每项核对报对；记录按来源分节写，其他节不动。"""

from __future__ import annotations

import json
import socket
from datetime import datetime
from pathlib import Path

from roundtable.core import installcheck as ic

ROOT = Path(__file__).resolve().parents[2]


def test_python_and_venv():
    assert ic.check_python((3, 12, 1)).ok
    old = ic.check_python((3, 9, 0))
    assert not old.ok and old.level == ic.BAD
    assert not ic.check_venv("/usr", "/usr").ok
    assert ic.check_venv("/p/.venv", "/usr").ok


def test_editable_points_at_this_project(tmp_path):
    assert ic.check_editable(ROOT).ok
    other = ic.check_editable(tmp_path)
    assert not other.ok and "不是这个目录" in other.title


def test_requirements_from_pyproject(tmp_path):
    reqs = ic.requirements(ROOT / "pyproject.toml", ["dev"])
    assert any(r.startswith("pydantic") for r in reqs) and any("pytest" in r for r in reqs)
    assert ic.check_requirements(["pydantic>=2", "pyyaml"])[0].ok
    items = {
        i.id: i
        for i in ic.check_requirements(
            ["no-such-package-zz>=1", "pydantic>=999", 'pywin32; sys_platform == "nonexistent"']
        )
    }
    assert (
        items["deps_missing"].level == ic.BAD
        and "no-such-package-zz" in items["deps_missing"].detail
    )
    assert items["deps_version"].level == ic.WARN
    assert "pywin32" not in json.dumps([i.view() for i in items.values()])


def test_files_and_port(tmp_path):
    assert ic.check_writable(tmp_path / "data").ok
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    assert not ic.check_writable(blocker / "sub").ok
    assert not ic.check_env_file(tmp_path).ok
    (tmp_path / ".env").write_text("", encoding="utf-8")
    assert ic.check_env_file(tmp_path).ok
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        busy = ic.check_port(port=port)
    assert not busy.ok and busy.level == ic.WARN
    assert ic.check_config().ok


def test_run_checks_and_record(tmp_path):
    items = ic.run_checks(ROOT, tmp_path, ["dev"], port=None)
    ids = {i.id for i in items}
    assert {"python", "venv", "editable", "data_dir", "env", "config", "sandbox"} <= ids
    now = datetime(2026, 11, 2, 9, 0)
    ic.save_record(tmp_path, "dsh", [{"id": "x", "ok": False, "title": "X"}], now)
    path = ic.save_record(tmp_path, "start", items, now)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"dsh", "start"} and data["start"]["at"] == "2026-11-02T09:00:00"
    said = []
    ic.print_items([ic.failed("a", "坏了", "原因", "这样修")], said.append)
    assert said == ["  ✗ 坏了：原因", "      怎么办：这样修"]
