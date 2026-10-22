"""办公室模式·开会（O3）：经理点名 → 员工依次发言 → 经理拍板写结论和纪要。"""

from __future__ import annotations

import pytest

from roundtable.core.cards import CardOption, ConfirmationCard
from roundtable.core.providers import ErrorKind
from roundtable.core.steps import NeedsApproval, StepFailed, final_answer, restore_state
from roundtable.core.steps import office as office_mod

from .conftest import REASONING, Table
from .test_office import MANAGER, check, intake, office_table, run

MINUTES = "## 结论\n最大值 2，最小值 -2。\n\n## 会议纪要\n组员甲、组员乙意见一致。"


def meeting(*names: str, assignee: str = "组员甲") -> str:
    return intake(assignee=assignee, mode="meeting", attendees=names, brief="议题：最值在哪里取得")


def set_cap(t: Table, usd: float) -> None:
    """只改这张桌子的配置副本（配置对象在测试之间共享）。"""
    t.ctx.config = t.config.model_copy(deep=True)
    object.__setattr__(t.ctx.config.roundtable.office.task_caps_usd, "meeting", usd)


async def test_meeting_speakers_talk_in_turn_and_manager_decides():
    t = office_table()
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"), MINUTES)
    t.fake.queue("b1", f"甲的发言。{REASONING}")
    t.fake.queue("b3", f"丙的发言。{REASONING}")
    results = await run(t, "intake", "handle", "check")
    o = t.ctx.state.office
    assert o.meeting and o.intake["attendees"] == ["甲", "丙"]
    assert list(o.speeches) and set(o.speeches) == {"甲", "丙"}
    # 只有点名的员工发言，各一次；没点名的乙一次都没被调用
    assert len(t.calls_for("b1")) == 1 and len(t.calls_for("b3")) == 1
    assert not t.calls_for("b2")
    # 后发言的看得到前面的发言；先发言的看不到后面的
    first, second = list(o.speeches)
    model = {"甲": "b1", "丙": "b3"}
    first_prompt = t.prompts_for(model[first])[0]
    second_prompt = t.prompts_for(model[second])[0]
    assert o.speeches[first][:4] in second_prompt
    assert o.speeches[second][:4] not in first_prompt
    assert "议题：最值在哪里取得" in first_prompt
    # 经理：分诊 + 拍板，两次；拍板看得到全部发言；不再过目
    manager = t.prompts_for(MANAGER)
    assert len(manager) == 2
    assert all(text[:4] in manager[1] for text in o.speeches.values())
    assert results[2].calls == 0 and o.check is None
    assert final_answer(t.ctx.state) == MINUTES
    assigned = [e for e in t.events if e.type == "office_assigned"]
    assert assigned[0].data["attendees"] == ["甲", "丙"]


async def test_speaking_order_is_reproducible_from_the_seed():
    orders = []
    for _ in range(2):
        t = office_table(seed=7)
        t.fake.queue(MANAGER, meeting("组员甲", "组员乙", "组员丙"), MINUTES)
        await run(t, "intake")
        t.ctx.rng.seed("7:0:handle")
        await run(t, "handle")
        orders.append(list(t.ctx.state.office.speeches))
    assert orders[0] == orders[1] and len(orders[0]) == 3


async def test_meeting_never_includes_the_manager():
    t = office_table()
    t.fake.queue(MANAGER, meeting("组员甲", "经理", "组员乙"))
    await run(t, "intake")
    assert t.ctx.state.office.intake["attendees"] == ["甲", "乙"]


async def test_too_few_named_attendees_are_filled_by_rule():
    t = office_table()
    t.fake.queue(MANAGER, meeting("组员丙", "组员戊"))
    (res,) = await run(t, "intake")
    o = t.ctx.state.office
    assert o.intake["attendees"] == ["丙", "甲"]  # 甲最便宜
    assert o.intake["attendees_by_rule"]
    assert any("按规则补齐" in n for n in res.notes)


async def test_attendees_are_capped_at_max():
    t = Table(coordinator=MANAGER, members={"甲": "b1", "乙": "b2", "丙": "b3", "丁": "f2"})
    t.fake.queue(MANAGER, meeting("组员甲", "组员乙", "组员丙", "组员丁"))
    await run(t, "intake")
    assert t.ctx.state.office.intake["attendees"] == ["甲", "乙", "丙"]


async def test_one_staff_cannot_hold_a_meeting():
    t = Table(coordinator=MANAGER, members={"甲": "b1"})
    t.fake.queue(MANAGER, meeting("组员甲", assignee="组员甲"))
    (res,) = await run(t, "intake")
    o = t.ctx.state.office
    assert o.intake["mode"] == "single" and not o.meeting
    assert any("没法开会" in n for n in res.notes)


async def test_meeting_over_cap_asks_the_boss():
    t = office_table()
    set_cap(t, 0.0001)
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"))
    with pytest.raises(NeedsApproval) as info:
        await run(t, "intake")
    card: ConfirmationCard = info.value.card
    assert card.kind == "office" and info.value.key == "office_group:0"
    assert card.option_keys() == ["meeting", "single", "stop"]
    assert "组员甲" in card.situation and "组员丙" in card.situation
    # 分诊已经存下：回复后重新执行不再调用经理
    calls = len(t.fake.calls)
    t.ctx.state = restore_state(t.repo, t.session, 0)
    t.ctx.approval = lambda key: "meeting"
    await run(t, "intake")
    assert len(t.fake.calls) == calls
    assert t.ctx.state.office.intake["approved"]
    assert t.ctx.state.extra_estimate_usd() > 0


async def test_boss_can_turn_the_meeting_into_single():
    t = office_table()
    set_cap(t, 0.0001)
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"), check())
    with pytest.raises(NeedsApproval):
        await run(t, "intake")
    t.ctx.approval = lambda key: "single"
    (res,) = await run(t, "intake")
    assert "改为单人办" in res.notes[0]
    await run(t, "handle", "check")
    o = t.ctx.state.office
    assert not o.meeting and o.worker == "甲" and o.speeches == {}
    assert final_answer(t.ctx.state) == o.work
    # 恢复后仍是单人办
    restored = restore_state(t.repo, t.session, 0).office
    assert restored.intake["mode"] == "single" and restored.intake["declined"] == "meeting"


async def test_budget_card_comes_first_when_budget_is_short():
    t = office_table()
    budget = ConfirmationCard(
        kind="budget",
        situation="预算不够",
        options=(CardOption("continue", "继续", 0.0), CardOption("stop", "停止", 0.0)),
        recommendation="stop",
    )
    t.ctx.budget_gate = lambda cost: budget
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"))
    with pytest.raises(NeedsApproval) as info:
        await run(t, "intake")
    assert info.value.card.kind == "budget"


async def test_failed_speaker_is_skipped_and_meeting_goes_on():
    t = office_table()
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"), MINUTES)
    t.fake.queue("b3", *[ErrorKind.SERVER] * 4)
    _, res = await run(t, "intake", "handle")
    o = t.ctx.state.office
    assert list(o.speeches) == ["甲"]
    assert any("没能发言" in n for n in res.notes)
    assert final_answer(t.ctx.state) == MINUTES


async def test_all_speakers_failing_fails_the_step():
    t = office_table()
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"))
    t.fake.queue("b1", *[ErrorKind.SERVER] * 4)
    t.fake.queue("b3", *[ErrorKind.SERVER] * 4)
    await run(t, "intake")
    with pytest.raises(StepFailed, match="都没能发言"):
        await run(t, "handle")


async def test_manager_failing_to_decide_delivers_the_speeches():
    t = office_table()
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"), *[ErrorKind.SERVER] * 4)
    _, res = await run(t, "intake", "handle")
    o = t.ctx.state.office
    assert o.decision["degraded"]
    final = final_answer(t.ctx.state)
    assert "经理没能写出结论" in final and "组员甲" in final and "组员丙" in final
    assert any("直接交付各位员工的发言" in n for n in res.notes)


async def test_meeting_restores_and_resumes_without_repeating_speeches():
    t = office_table()
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"), *[ErrorKind.SERVER] * 4)
    await run(t, "intake", "handle")
    restored = restore_state(t.repo, t.session, 0)
    o = t.ctx.state.office
    assert restored.office.speeches == o.speeches
    assert restored.office.decision == o.decision
    assert restored.office.intake == o.intake
    before = len(t.fake.calls)
    t.ctx.state = restore_state(t.repo, t.session, 0)
    await run(t, "intake", "handle", "check")
    assert len(t.fake.calls) == before


async def test_meeting_speakers_get_no_tools(monkeypatch):
    seen = []
    real = office_mod.call_model

    async def spy(ctx, **kw):
        seen.append(kw["step"])
        return await real(ctx, **kw)

    monkeypatch.setattr(office_mod, "call_model", spy)
    t = office_table()
    t.fake.queue(MANAGER, meeting("组员甲", "组员丙"), MINUTES)
    await run(t, "intake", "handle")
    assert seen == ["speak", "speak", "decide"]
    # 发言和拍板的步骤没有配置工具
    assert not t.config.roundtable.tools.by_step.get("speak")
    assert not t.config.roundtable.tools.by_step.get("decide")
