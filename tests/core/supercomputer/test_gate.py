"""自治闸门：风险分级、你发起的直接干、定时活看闸门和每天上限、高风险永远问你。"""

from __future__ import annotations

import pytest

from roundtable.core.config.schema import SuperGate
from roundtable.core.supercomputer.gate import decide, risk


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("调研一下竞品，总结成表格", "low"),
        ("检查测试覆盖率", "low"),
        ("给接口加分页，补测试", "medium"),
        ("查一下慢查询并修好", "medium"),  # 查 + 修：有改动就是中
        ("整理今天的笔记", "medium"),  # 什么词都没碰到：保守算中
        ("改完直接 git push 到 main", "high"),
        ("删除旧的日志文件", "high"),
        ("把 .env 里的值打印出来", "high"),
    ],
)
def test_risk(text, level):
    assert risk(text, SuperGate())[0] == level


def test_manual_always_runs():
    v = decide("删除旧分支", SuperGate(autonomy="off"), source="manual", auto_used=99)
    assert v.run and v.risk == "high" and "你发起的" in v.why


def test_schedule_gate_levels():
    low, mid, high = "调研竞品", "给接口加分页", "部署到服务器"
    g = SuperGate(autonomy="low", max_auto_per_day=2)
    assert decide(low, g, source="schedule", auto_used=0).run
    v = decide(mid, g, source="schedule", auto_used=0)
    assert not v.run and "只放行" in v.why
    v = decide(high, SuperGate(autonomy="medium"), source="schedule", auto_used=0)
    assert not v.run and "一定要你点头" in v.why and v.hits == ("部署",)
    assert decide(mid, SuperGate(autonomy="medium"), source="schedule", auto_used=0).run
    v = decide(low, SuperGate(autonomy="off"), source="schedule", auto_used=0)
    assert not v.run and "都等你确认" in v.why
    v = decide(low, g, source="schedule", auto_used=2)
    assert not v.run and "用完" in v.why
