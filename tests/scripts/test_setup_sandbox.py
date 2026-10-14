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


def test_parse_sha256sum_handles_both_formats():
    digest = "940b4c7c8531687e6a621cce3c8e0efa530d08a639dadf70697bb2788f9c35b0"
    unix = f"{digest}  deno-x86_64-unknown-linux-gnu.zip\n"
    # Windows 版发行包附带的是 PowerShell Get-FileHash 的输出（哈希为大写）
    windows = (
        "\nAlgorithm : SHA256\n"
        f"Hash      : {digest.upper()}\n"
        "Path      : C:\\a\\deno\\deno\\target\\release\\deno-x86_64-pc-windows-msvc.zip\n\n"
    )
    assert setup.parse_sha256sum(unix) == digest
    assert setup.parse_sha256sum(windows.encode()) == digest
    for bad in ("", "no hash here", f"{digest}\n{'a' * 64}\n"):
        with pytest.raises(SystemExit):
            setup.parse_sha256sum(bad)
