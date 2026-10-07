"""架构守卫：core 不依赖 Web/界面框架；仓库中没有密钥。"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CORE = ROOT / "src" / "roundtable" / "core"
FORBIDDEN_IN_CORE = {"fastapi", "starlette", "uvicorn", "streamlit"}

SECRET_PATTERNS = [
    re.compile(r"sk-or-v1-[0-9a-f]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    re.compile(r"xai-[A-Za-z0-9]{32,}"),
    # 形如 FOO_API_KEY=xxx 的非空赋值
    re.compile(r"^[A-Z0-9_]*API_KEY[ \t]*=[ \t]*[^\s#]+", re.MULTILINE),
]


def forbidden_imports(source: str, forbidden: set[str]) -> set[str]:
    """返回源码中导入的、属于 forbidden 的顶层模块名。"""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        else:
            continue
        found |= {n.split(".")[0] for n in names} & forbidden
    return found


def repo_files() -> list[Path]:
    """已跟踪文件 + 未被忽略的新文件（即将被提交的范围）。"""
    out = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / line for line in out.splitlines() if (ROOT / line).is_file()]


def find_secrets(text: str) -> list[str]:
    return [m.group(0)[:12] + "…" for p in SECRET_PATTERNS for m in p.finditer(text)]


# --- 守卫本身的单元测试 -------------------------------------------------------


@pytest.mark.parametrize(
    "source, expected",
    [
        ("import fastapi", {"fastapi"}),
        ("from starlette.requests import Request", {"starlette"}),
        ("import os, uvicorn", {"uvicorn"}),
        ("import streamlit as st", {"streamlit"}),
        ("from . import fastapi", set()),  # 相对导入不是第三方包
        ("import pydantic\nfrom httpx import Client", set()),
    ],
)
def test_forbidden_imports_detection(source, expected):
    assert forbidden_imports(source, FORBIDDEN_IN_CORE) == expected


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("key = 'sk-or-v1-" + "0123456789abcdef0123'", True),
        ("OPENROUTER_API_KEY=abc123", True),
        ("OPENROUTER_API_KEY=", False),
        ("OPENROUTER_API_KEY=\n\n# 注释", False),
        ("OPENROUTER_API_KEY = abc  # x", True),
        ("headers = {'Authorization': f'Bearer {api_key}'}", False),
    ],
)
def test_secret_detection(text, leaked):
    assert bool(find_secrets(text)) is leaked


# --- 真正的仓库检查 -----------------------------------------------------------


def test_core_does_not_import_web_frameworks():
    violations = {}
    for path in CORE.rglob("*.py"):
        found = forbidden_imports(path.read_text(encoding="utf-8"), FORBIDDEN_IN_CORE)
        if found:
            violations[str(path.relative_to(ROOT))] = sorted(found)
    assert violations == {}


def test_repo_contains_no_secrets():
    leaks = {}
    for path in repo_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits = find_secrets(text)
        if hits:
            leaks[str(path.relative_to(ROOT))] = hits
    assert leaks == {}


def test_env_is_gitignored_and_example_is_tracked():
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=ROOT, check=False
    ).returncode
    assert ignored == 0, ".env 必须在 .gitignore 中"
    example = subprocess.run(
        ["git", "check-ignore", "-q", ".env.example"], cwd=ROOT, check=False
    ).returncode
    assert example != 0, ".env.example 不应被忽略"
