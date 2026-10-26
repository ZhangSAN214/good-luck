"""革新例会：资料卡作为附件走头脑风暴（全员上桌、匿名）、花费上限、导出采纳单、每周自动开一次。"""

from __future__ import annotations

from datetime import datetime

import pytest

from roundtable.core.config.schema import EvolutionRules
from roundtable.core.service import RoundtableService, ServiceError

from ..orchestrator.test_brainstorm import make as brainstorm_env
from .test_service import TAROT, leaks

MONDAY_10 = datetime(2026, 10, 26, 10, 0).astimezone()  # 本机时区（自动开会按本机时间）
MONDAY_8 = datetime(2026, 10, 26, 8, 0).astimezone()
CARD = "# 半衰期记忆\n来源：某开源项目（MIT，3k star）\n做法：记忆按时间衰减，常用的保留。"


def make(tmp_path, cards=(("1-memory.md", CARD),), **rules):
    env = brainstorm_env()
    update = {"enabled": True, "topic": "请给圆桌提改进建议。", **rules}
    rt_cfg = env.config.roundtable.model_copy(update={"evolution": EvolutionRules(**update)})
    env.config = env.config.model_copy(update={"roundtable": rt_cfg})
    env.rt.config = env.config
    env.rt.evolution_dir = tmp_path
    inbox = tmp_path / "收件箱"
    inbox.mkdir(parents=True)
    for name, text in cards:
        (inbox / name).write_text(text, encoding="utf-8")
    return env, RoundtableService(env.rt)


async def test_meeting_runs_brainstorm_with_cards_for_everyone(tmp_path):
    env, svc = make(tmp_path)
    info = svc.evolution()
    assert info["enabled"] and [c["title"] for c in info["cards"]] == ["半衰期记忆"]
    started = svc.evolution_meet()
    sid = started["session_id"]
    assert started["estimate_usd"] <= 0.40
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "completed" and data["workflow"] == "brainstorm"
    assert data["anonymous"] and leaks(env, data) == []
    assert set(data["tables"][0]["codes"]) <= TAROT
    assert [a["name"] for a in data["attachments"]] == ["1-memory.md"]
    # 开过会的卡移到 已用/<周>/，下周不会再读
    assert not (tmp_path / "收件箱" / "1-memory.md").exists()
    assert (tmp_path / "已用" / started["week"] / "1-memory.md").read_text(encoding="utf-8") == CARD
    assert svc.evolution()["cards"] == []
    # 每位提点子的成员都收到同一份资料卡
    ideate = [c for c in env.fake.calls if "其他人这一轮看不到你的点子" in c.messages[0].content]
    assert len(ideate) >= 2
    assert all("记忆按时间衰减" in c.messages[1].content for c in ideate)
    assert len({c.messages[1].content for c in ideate}) == 1  # 同一份题目与附件

    out = svc.evolution_export(sid)
    assert out["file"] == f"{started['week']}.md"
    saved = (tmp_path / "采纳单" / out["file"]).read_text(encoding="utf-8")
    assert saved == out["markdown"] and "### 1." in saved and "1-memory.md" in saved
    assert leaks(env, saved) == []
    info = svc.evolution()
    assert info["meetings"][0]["adopted"] == out["file"]
    assert info["met_this_week"] and info["month_spent_usd"] > 0


async def test_disabled_or_empty_inbox_refuses(tmp_path):
    _, svc = make(tmp_path, enabled=False, topic="")
    with pytest.raises(ServiceError, match="没有开启"):
        svc.evolution_meet()
    _, svc = make(tmp_path / "b", cards=())
    with pytest.raises(ServiceError, match="没有资料卡"):
        svc.evolution_meet()


async def test_caps_block_meeting_before_any_call(tmp_path):
    env, svc = make(tmp_path, meeting_usd=0.000001, monthly_usd=1.5)
    with pytest.raises(ServiceError, match="每场上限"):
        svc.evolution_meet()
    assert env.fake.calls == []


async def test_monthly_cap_counts_earlier_meetings(tmp_path):
    env, svc = make(tmp_path)
    sid = svc.evolution_meet()["session_id"]
    await svc.wait(sid)
    (tmp_path / "收件箱" / "2-again.md").write_text(CARD, encoding="utf-8")
    spent = svc.evolution()["month_spent_usd"]
    rules = env.config.roundtable.evolution.model_copy(update={"monthly_usd": spent + 0.001})
    env.rt.config = env.config.model_copy(
        update={"roundtable": env.config.roundtable.model_copy(update={"evolution": rules})}
    )
    with pytest.raises(ServiceError, match="本月革新例会已花"):
        svc.evolution_meet()


async def test_export_rules(tmp_path):
    env, svc = make(tmp_path)
    other = svc.create("别的题目", seed=1)
    await svc.wait(other)
    with pytest.raises(ServiceError, match="不是革新例会"):
        svc.evolution_export(other)


async def test_tick_meets_once_per_week_after_slot_and_exports(tmp_path):
    env, svc = make(tmp_path)
    early = svc.evolution_tick(MONDAY_8)
    assert early["started"] is None and env.fake.calls == []
    first = svc.evolution_tick(MONDAY_10)
    assert first["started"]
    await svc.wait(first["started"])
    second = svc.evolution_tick(MONDAY_10)
    assert second["started"] is None and second["exported"] == ["2026-W44.md"]
    assert svc.evolution_tick(MONDAY_10) == {"started": None, "exported": [], "skipped": None}
    with pytest.raises(ServiceError, match="已经开过"):
        svc.evolution_meet(auto=True, now=MONDAY_10)


async def test_tick_does_nothing_when_off(tmp_path):
    env, svc = make(tmp_path, auto=False)
    assert svc.evolution_tick(MONDAY_10)["started"] is None
    assert env.fake.calls == []


async def test_decide_do_or_skip_writes_the_todo_list(tmp_path):
    env, svc = make(tmp_path)
    sid = svc.evolution_meet()["session_id"]
    await svc.wait(sid)
    [meeting] = svc.evolution()["meetings"]
    ideas = meeting["ideas"]
    assert ideas and all(i["decision"] is None for i in ideas)
    assert leaks(env, ideas) == []
    first = ideas[0]
    out = svc.evolution_decide(sid, first["id"], "do")
    todo = (tmp_path / "待实现.md").read_text(encoding="utf-8")
    assert out["todo"].endswith("待实现.md") and first["text"] in todo
    assert svc.evolution()["meetings"][0]["ideas"][0]["decision"] == "do"
    svc.evolution_decide(sid, first["id"], "skip")  # 改主意
    assert first["text"] not in (tmp_path / "待实现.md").read_text(encoding="utf-8")
    with pytest.raises(ServiceError, match="只能是"):
        svc.evolution_decide(sid, first["id"], "maybe")
    with pytest.raises(ServiceError, match="没有这条"):
        svc.evolution_decide(sid, "I999", "do")
    other = svc.create("别的题目", seed=1)
    await svc.wait(other)
    with pytest.raises(ServiceError, match="不是革新例会"):
        svc.evolution_decide(other, first["id"], "do")
