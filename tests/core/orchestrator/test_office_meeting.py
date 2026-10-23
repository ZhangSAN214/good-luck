"""办公室模式·开会（O3）走完整流程：经理决定开会 → 超过开会上限时弹卡片 → 开会或改单人办。"""

from __future__ import annotations

from roundtable.core.config.schema import OfficeTaskCaps
from roundtable.core.service import RoundtableService
from roundtable.core.steps import restore_state

from .conftest import MEDIUM, Env


def make(meeting_cap: float = 0.15) -> tuple[Env, RoundtableService]:
    office = Env().config.roundtable.office.model_copy(
        update={"task_caps_usd": OfficeTaskCaps(single=100.0, meeting=meeting_cap)}
    )
    env = Env(confirm_threshold_usd=100.0, rt_update={"office": office})
    env.office_mode = "meeting"
    return env, RoundtableService(env.rt)


def speakers(env: Env) -> list[str]:
    return env.models_called("参会人之一")


async def test_meeting_runs_end_to_end_under_the_cap():
    env, svc = make()
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "completed"
    assert len(speakers(env)) == 2
    kinds = [o["kind"] for o in data["outputs"]]
    assert kinds.count("office_speech") == 2 and "office_decision" in kinds
    assert "office_check" not in kinds and "office_work" not in kinds
    assert restore_state(env.rt.repo, sid, 0).office.final.startswith("## 结论")
    # 超支保护把开会的预估计入，不会在跳过的「过目」前弹卡片
    assert not [c for c in data["checkpoints"] if c["kind"] == "overrun"]


async def test_meeting_over_cap_asks_then_meets():
    env, svc = make(meeting_cap=0.000001)
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "awaiting_confirmation"
    card = data["pending_checkpoint"]
    assert card["kind"] == "office"
    assert [o["key"] for o in card["card"]["options"]] == ["meeting", "single", "stop"]
    managers_before = len(env.models_called("派给合适的员工"))
    svc.respond(sid, "meeting")
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "completed"
    assert len(speakers(env)) == 2
    # 回复后不重新分诊
    assert len(env.models_called("派给合适的员工")) == managers_before


async def test_boss_turns_meeting_into_single():
    env, svc = make(meeting_cap=0.000001)
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    svc.respond(sid, "single")
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "completed"
    assert speakers(env) == []
    kinds = [o["kind"] for o in data["outputs"]]
    assert "office_work" in kinds and "office_check" in kinds
    office = restore_state(env.rt.repo, sid, 0).office
    assert office.intake["mode"] == "single" and office.intake["declined"] == "meeting"


async def test_stop_on_the_meeting_card_stops():
    env, svc = make(meeting_cap=0.000001)
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    svc.respond(sid, "stop")
    await svc.wait(sid)
    assert svc.session(sid)["status"] == "stopped"
    assert speakers(env) == []
