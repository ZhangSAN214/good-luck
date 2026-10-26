"""革新例会·插件清单：开完会由最便宜的一位成员写 ≥ count 个，不够再补写（内容不同），遮蔽身份、
花费计入每月上限、存到数据目录、自动只写一次。"""

from __future__ import annotations

import json
import re

import pytest

from roundtable.core.evolution_plugins import PLUGINS_KEY
from roundtable.core.service import ServiceError

from .test_evolution_service import make
from .test_service import leaks

WRITE = "整理一份「插件清单」"


def plugin_writer(env, first=12):
    """第一次只写 first 个（其中一个缺字段、一个自报模型 id），之后按要求的个数补写。"""
    original = env.fake._default
    state = {"n": 0}

    def reply(model, messages):
        if WRITE not in messages[0].content:
            return original(model, messages)
        want = int(re.search(r"请写 (\d+) 个插件", messages[1].content).group(1))
        start, state["n"] = state["n"], state["n"] + (first if state["n"] == 0 else want)
        items = [
            {
                "name": f"插件{start + i}",
                "scene": ["办公室工作", "公寓生活", "群聊", "火星"][i % 4],
                "what": f"我是 {model}，做一件事",
                "use": "有用",
                "pros": "好",
                "cons": "花钱",
                "why": "现在不够",
                "source": "资料卡 1",
            }
            for i in range(state["n"] - start)
        ]
        if start == 0:
            items[0]["why"] = ""
        return json.dumps({"plugins": items}, ensure_ascii=False)

    env.fake._default = reply


async def meeting(tmp_path, **rules):
    env, svc = make(tmp_path, **rules)
    sid = svc.evolution_meet()["session_id"]
    await svc.wait(sid)
    plugin_writer(env)
    return env, svc, sid


async def test_writes_at_least_count_plugins_with_one_cheapest_member(tmp_path):
    env, svc, sid = await meeting(tmp_path)
    before = svc.evolution()["month_spent_usd"]
    out = await svc.evolution_plugins(sid)
    assert len(out["plugins"]) >= 20
    # 只有一位成员写；两次请求内容不同（不原样重试），第二次附上已写的名字
    calls = [c for c in env.fake.calls if WRITE in c.messages[0].content]
    assert len(calls) == 2 and len({c.model for c in calls}) == 1
    assert calls[0].messages[1].content != calls[1].messages[1].content
    assert "插件1（" in calls[1].messages[1].content
    # 资料卡和点子都在标签里交给成员
    first = calls[0].messages[1].content
    assert "<cards>" in first and "记忆按时间衰减" in first and "<ideas>" in first
    # 场景不在清单里的归到兜底场景；成员自报的模型 id 被遮蔽
    scenes = {p["scene"] for p in out["plugins"]}
    assert "火星" not in scenes and "通用" in scenes
    assert leaks(env, out) == []
    # 存到数据目录，网页能看到，花费计入本月革新例会
    saved = (tmp_path / "插件清单" / out["file"]).read_text(encoding="utf-8")
    assert saved == out["markdown"] and "**优点**" in saved
    [m] = svc.evolution()["meetings"]
    assert len(m["plugins"]["items"]) == len(out["plugins"]) and m["plugins"]["scenes"]
    assert leaks(env, m["plugins"]) == []
    assert svc.evolution()["month_spent_usd"] == pytest.approx(before + out["cost_usd"])
    # 导出的采纳单提到插件清单
    assert "插件清单/" in svc.evolution_export(sid)["markdown"]


async def test_refuses_before_completion_or_over_budget(tmp_path):
    env, svc = make(tmp_path)
    other = svc.create("别的题目", seed=1)
    await svc.wait(other)
    with pytest.raises(ServiceError, match="不是革新例会"):
        await svc.evolution_plugins(other)
    env, svc, sid = await meeting(tmp_path / "b")
    spent = svc.evolution()["month_spent_usd"]
    rules = env.config.roundtable.evolution.model_copy(update={"monthly_usd": spent + 0.001})
    env.rt.config = env.config.model_copy(
        update={"roundtable": env.config.roundtable.model_copy(update={"evolution": rules})}
    )
    with pytest.raises(ServiceError, match="剩余额度"):
        await svc.evolution_plugins(sid)
    assert not [c for c in env.fake.calls if WRITE in c.messages[0].content]
    # 失败记下来，网页显示原因
    assert "剩余额度" in svc.evolution()["meetings"][0]["plugins"]["error"]


async def test_meeting_cap_includes_plugin_estimate(tmp_path):
    env, svc = make(tmp_path)
    est = svc.evolution_meet()["estimate_usd"]
    assert est >= env.config.roundtable.evolution.plugins.estimate_usd


async def test_tick_writes_once_per_meeting(tmp_path):
    env, svc, sid = await meeting(tmp_path)
    assert await svc.evolution_plugins_tick() == [sid]
    n = len(env.fake.calls)
    assert await svc.evolution_plugins_tick() == [] and len(env.fake.calls) == n
    assert sid in json.loads(env.rt.repo.setting(PLUGINS_KEY))


async def test_tick_off_when_disabled(tmp_path):
    env, svc, sid = await meeting(tmp_path, auto=False)
    assert await svc.evolution_plugins_tick() == []
