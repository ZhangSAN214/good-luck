"""roundtable backup：列表、马上备份、存档点、恢复要输入 yes（--yes 跳过）。"""

from __future__ import annotations

import io

from roundtable.cli import run

from ..core.orchestrator.conftest import Env
from .test_cli import Answers


async def _cli(env, *argv, answers=None):
    out = io.StringIO()
    code = await run(list(argv), runtime=env.rt, out=out, ask=answers or Answers())
    return code, out.getvalue()


async def test_backup_commands(tmp_path):
    env = Env()
    env.rt.backup_dir = tmp_path / "bk"
    code, text = await _cli(env, "backup")
    assert code == 0 and "还没有备份" in text
    code, text = await _cli(env, "backup", "now")
    assert code == 0 and "已备份" in text
    code, text = await _cli(env, "backup", "checkpoint", "试跑前")
    assert code == 0 and "试跑前" in text
    name = text.split("：", 1)[1].strip()
    code, text = await _cli(env, "backup", "restore", name, answers=Answers("no"))
    assert code == 1 and "没有恢复" in text
    code, text = await _cli(env, "backup", "restore", name, answers=Answers("yes"))
    assert code == 0 and "已恢复" in text
    code, text = await _cli(env, "backup", "restore", "../x.db", "--yes")
    assert code == 1 and "备份出错" in text
    code, text = await _cli(env, "backup")
    assert "存档点·恢复前" in text
