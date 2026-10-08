from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "lock_prompts.py"
spec = importlib.util.spec_from_file_location("lock_prompts_t", SCRIPT)
lock_prompts = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = lock_prompts
spec.loader.exec_module(lock_prompts)

TEMPLATE = """---
variables: [q]
---
<!-- system -->
system {n}
<!-- user -->
{{{{ q }}}}
"""


def write(root: Path, name: str, n: int = 1) -> None:
    role, version = name.split("/")
    (root / role).mkdir(parents=True, exist_ok=True)
    (root / role / f"{version}.md").write_text(TEMPLATE.format(n=n), encoding="utf-8")


def lock(root: Path) -> dict:
    return json.loads((root / "versions.lock").read_text(encoding="utf-8"))


def test_registers_new_versions(tmp_path, capsys):
    write(tmp_path, "a/v1")
    assert lock_prompts.main([], root=tmp_path) == 0
    assert set(lock(tmp_path)) == {"a/v1"}
    write(tmp_path, "a/v2", 2)
    assert lock_prompts.main([], root=tmp_path) == 0
    assert set(lock(tmp_path)) == {"a/v1", "a/v2"}


def test_check_mode_flags_unregistered_without_writing(tmp_path):
    write(tmp_path, "a/v1")
    assert lock_prompts.main(["--check"], root=tmp_path) == 1
    assert not (tmp_path / "versions.lock").exists()


def test_refuses_modified_published_version(tmp_path, capsys):
    write(tmp_path, "a/v1")
    lock_prompts.main([], root=tmp_path)
    before = lock(tmp_path)
    write(tmp_path, "a/v1", 99)
    assert lock_prompts.main([], root=tmp_path) == 1
    assert lock(tmp_path) == before
    assert "已发布后被修改" in capsys.readouterr().out


def test_refuses_deleted_published_version(tmp_path, capsys):
    write(tmp_path, "a/v1")
    lock_prompts.main([], root=tmp_path)
    (tmp_path / "a" / "v1.md").unlink()
    assert lock_prompts.main(["--check"], root=tmp_path) == 1
    assert "不能删除" in capsys.readouterr().out


def test_clean_state(tmp_path):
    write(tmp_path, "a/v1")
    lock_prompts.main([], root=tmp_path)
    assert lock_prompts.main(["--check"], root=tmp_path) == 0
