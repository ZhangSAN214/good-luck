from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "setup_sandbox", ROOT / "scripts" / "setup_sandbox.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)  # type: ignore[union-attr]


def test_resolve_follows_dependencies():
    lock = {"packages": {"a": {"depends": ["b"]}, "b": {"depends": []}, "c": {"depends": []}}}
    assert setup.resolve(lock, ["a"]) == ["a", "b"]
    with pytest.raises(SystemExit):
        setup.resolve(lock, ["missing"])


def test_names_and_targets():
    assert setup.norm("Python_Docx") == "python-docx"
    target = setup.deno_target()
    assert target.split("-", 1)[0] in ("x86_64", "aarch64")


def test_refuses_project_directory(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["setup_sandbox.py", "--dir", str(ROOT / "data" / "sb")])
    with pytest.raises(SystemExit, match="项目目录"):
        setup.main()
