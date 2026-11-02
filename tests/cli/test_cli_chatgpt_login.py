"""roundtable chatgpt-login：你自己登录一次 ChatGPT 网页，成功后在本机打开 chatgpt-web。"""

from __future__ import annotations

import argparse
import io
import shutil

from roundtable.cli import build_parser, cmd_chatgpt_login
from roundtable.core.config.loader import DEFAULT_CONFIG_DIR


def _folder(tmp_path):
    folder = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR, folder, ignore=shutil.ignore_patterns("*.local.yaml"))
    return folder


def _args(*argv) -> argparse.Namespace:
    return build_parser().parse_args(["chatgpt-login", *argv])


async def test_login_success_switches_the_model_on(tmp_path):
    folder = _folder(tmp_path)
    seen = {}

    async def login(url, params, *, wait_s):
        seen.update(url=url, params=params, wait_s=wait_s)
        return True

    out = io.StringIO()
    code = await cmd_chatgpt_login(_args("--wait", "30"), out, login=login, config_dir=folder)
    assert code == 0 and "登录成功" in out.getvalue() and "自己登录" in out.getvalue()
    assert seen["url"] == "https://chatgpt.com" and seen["wait_s"] == 30
    assert "chatgpt-web: true" in (folder / "models.local.yaml").read_text(encoding="utf-8")

    out = io.StringIO()
    assert await cmd_chatgpt_login(_args("--off"), out, config_dir=folder) == 0
    assert "chatgpt-web: false" in (folder / "models.local.yaml").read_text(encoding="utf-8")


async def test_login_not_finished_changes_nothing(tmp_path):
    folder = _folder(tmp_path)

    async def login(url, params, *, wait_s):
        return False

    out = io.StringIO()
    assert await cmd_chatgpt_login(_args(), out, login=login, config_dir=folder) == 1
    assert "没等到" in out.getvalue()
    assert not (folder / "models.local.yaml").exists()


async def test_missing_playwright_is_explained(tmp_path):
    async def login(url, params, *, wait_s):
        raise ImportError("playwright")

    out = io.StringIO()
    assert await cmd_chatgpt_login(_args(), out, login=login, config_dir=_folder(tmp_path)) == 1
    assert "pip install playwright" in out.getvalue()
