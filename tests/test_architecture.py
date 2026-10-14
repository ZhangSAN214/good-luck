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
# 只有这些文件可以调用 Secret.reveal()：适配器把 key 放进请求头，secrets 模块自身用于脱敏
REVEAL_ALLOWED = {
    "src/roundtable/core/providers/secrets.py",
    "src/roundtable/core/providers/openai_compat.py",
    "src/roundtable/core/providers/gemini.py",
    "src/roundtable/core/providers/anthropic_adapter.py",
    "src/roundtable/core/search/tavily.py",
    "src/roundtable/core/search/openrouter.py",
}

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


def reveal_calls(source: str) -> int:
    return sum(
        1
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "reveal"
    )


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


def test_reveal_only_in_allowed_files():
    """key 的明文只允许在少数文件中取出，防止被写进日志、数据库或返回给前端。"""
    offenders = {}
    for path in (ROOT / "src").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        count = reveal_calls(path.read_text(encoding="utf-8"))
        if count and rel not in REVEAL_ALLOWED:
            offenders[rel] = count
    assert offenders == {}


def test_reveal_guard_detects_calls():
    assert reveal_calls("key.reveal()\nx = s.reveal()") == 2
    assert reveal_calls("reveal = 1\nprint(reveal)") == 0


# 分配与路由属于"决策逻辑"，不得写死任何模型 / 厂商 / 别称（适配器按协议命名，不在此列）
DECISION_PACKAGES = [
    "allocation",
    "routing",
    "prompts",
    "budget",
    "storage",
    "steps",
    "orchestrator",
]


def identity_terms() -> set[str]:
    from roundtable.core.config import load_config

    cfg = load_config()
    terms = set()
    for m in cfg.models.models:
        terms |= {m.id, m.vendor, *m.aliases, *(r.model for r in m.routes)}
    return {t.lower() for t in terms if len(t) >= 3}


def string_constants(source: str) -> list[str]:
    return [
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_decision_code_has_no_brand_names():
    terms = identity_terms()
    hits = {}
    for package in DECISION_PACKAGES:
        for path in (CORE / package).rglob("*.py"):
            for text in string_constants(path.read_text(encoding="utf-8")):
                found = sorted(t for t in terms if t in text.lower())
                if found:
                    hits.setdefault(str(path.relative_to(ROOT)), []).extend(found)
    assert hits == {}


def test_frontend_has_no_brand_or_channel_names():
    """前端只显示服务端给的内容，代码里不能写死任何模型、厂商或渠道名。"""
    from roundtable.core.config import load_config

    terms = identity_terms() | {c.lower() for c in load_config().models.channels}
    hits = {}
    for path in (ROOT / "web").rglob("*"):
        if path.suffix not in {".html", ".js", ".css"}:
            continue
        text = path.read_text(encoding="utf-8").lower()
        found = sorted(
            t for t in terms if re.search(rf"(?<![0-9a-z]){re.escape(t)}(?![0-9a-z])", text)
        )
        if found:
            hits[str(path.relative_to(ROOT))] = found
    assert hits == {}


def roundtable_imports(source: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        else:
            continue
        found |= {n for n in names if n.startswith("roundtable")}
    return found


def test_api_only_uses_service():
    """Web 层只能通过 core.service 访问核心逻辑（构建运行时除外）。"""
    allowed = {"roundtable.core.service", "roundtable.core.runtime"}
    for path in (ROOT / "src" / "roundtable" / "api").rglob("*.py"):
        extra = roundtable_imports(path.read_text(encoding="utf-8")) - allowed
        assert extra == set(), f"{path.name} 直接导入了 {sorted(extra)}"


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
