"""roundtable kit-check：不需要配置和模型，没法检查时返回 2。"""

from __future__ import annotations

import io

import pytest

from roundtable.cli import run


async def test_kit_check_missing_file_needs_no_config(tmp_path):
    pytest.importorskip("playwright.sync_api")
    out = io.StringIO()
    code = await run(["kit-check", str(tmp_path / "nope.html")], out=out)
    assert code == 2
    assert "找不到文件" in out.getvalue()
