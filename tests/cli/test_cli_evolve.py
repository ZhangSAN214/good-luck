"""命令行 roundtable evolve：概况、开会并导出采纳单、默认关闭时说明怎么开。"""

from __future__ import annotations

import re

from ..core.orchestrator.conftest import Env
from ..core.service.test_evolution_service import make
from .test_cli import cli


async def test_evolve_status_when_off():
    code, out = await cli(Env(), "evolve")
    assert code == 0 and "革新例会：关闭" in out and "evolution.enabled" in out


async def test_evolve_meet_without_enabling_explains():
    code, out = await cli(Env(), "evolve", "meet")
    assert code == 2 and "没有开启" in out


async def test_evolve_meet_saves_adoption_list(tmp_path):
    env, _ = make(tmp_path)
    code, out = await cli(env, "evolve", "meet")
    assert code == 0, out
    assert "1 张资料卡" in out and "采纳单已保存到" in out
    assert list((tmp_path / "采纳单").glob("*.md"))
    code, out = await cli(env, "evolve", "status")
    # 开过会的卡已移到 已用/，概况里列出得票前几名
    # 第一名可能是原点子（I）也可能是杂交点子（E），看随机种子
    assert "没有资料卡" in out and "采纳单" in out and re.search(r"\n    1\. [IE]\d", out)
    code, out = await cli(env, "evolve", "export")
    assert code == 2 and "会话 id" in out
