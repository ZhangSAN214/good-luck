"""办公室模式：经理分诊派活 → 员工办理 → 经理过目（退回改一次）。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.attachments import Attachment
from roundtable.core.providers import ErrorKind
from roundtable.core.steps import StepFailed, final_answer, get_step, restore_state
from roundtable.core.steps import office as office_mod

from .conftest import REASONING, Table

MANAGER = "f1"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def office_table(seed: int = 0) -> Table:
    return Table(seed, coordinator=MANAGER)


async def run(t: Table, *steps: str):
    return [await get_step(s).run(t.ctx) for s in steps]


def intake(assignee="组员乙", mode="single", needs_search=False, brief="写清推导和结论") -> str:
    return json.dumps(
        {
            "mode": mode,
            "assignee": assignee,
            "needs_search": needs_search,
            "brief": brief,
            "reason": "对口且便宜",
        },
        ensure_ascii=False,
    )


def check(verdict="pass", issues=()) -> str:
    return json.dumps(
        {"verdict": verdict, "issues": list(issues), "note": "核对了结论"}, ensure_ascii=False
    )


@pytest.fixture
def recorded(monkeypatch):
    """记录每次员工调用去掉了哪些工具。"""
    seen: list[tuple[str, tuple[str, ...]]] = []
    real = office_mod.call_model

    async def spy(ctx, **kw):
        seen.append((kw["step"], tuple(kw.get("exclude_tools", ()))))
        return await real(ctx, **kw)

    monkeypatch.setattr(office_mod, "call_model", spy)
    return seen


async def test_manager_assigns_one_staff_who_does_the_work(recorded):
    t = office_table()
    t.fake.queue(MANAGER, intake(), check())
    await run(t, "intake", "handle", "check")
    o = t.ctx.state.office
    assert o.intake["assignee"] == "乙" and not o.intake["by_rule"]
    assert o.worker == "乙"
    # 只有被派到的员工干活，其余员工一次都没被调用
    assert len(t.calls_for("b2")) == 1
    assert not t.calls_for("b1") and not t.calls_for("b3")
    # 经理只分诊和过目
    assert len(t.calls_for(MANAGER)) == 2
    assert "写清推导和结论" in t.prompts_for("b2")[0]
    assert final_answer(t.ctx.state) == o.work
    # 经理没说要查资料：去掉 search / fetch
    assert recorded == [("handle", ("search", "fetch"))]
    assigned = [e for e in t.events if e.type == "office_assigned"]
    assert len(assigned) == 1


async def test_staff_list_shows_every_staff_with_tags_tier_and_wage():
    t = office_table()
    t.fake.queue(MANAGER, intake(), check())
    await run(t, "intake")
    prompt = t.prompts_for(MANAGER)[0]
    for code in ("甲", "乙", "丙"):
        assert f"组员{code}" in prompt
    assert "擅长：math、vision" in prompt and "$0.3 / $1.2" in prompt
    # 发给经理的内容不含模型 id
    assert "b1" not in prompt and "b2" not in prompt


async def test_search_tools_given_only_when_manager_says_so(recorded):
    t = office_table()
    t.fake.queue(MANAGER, intake(needs_search=True), check())
    await run(t, "intake", "handle")
    assert recorded == [("handle", ())]


async def test_revise_sends_back_once_and_fixed_version_is_delivered(recorded):
    t = office_table()
    t.fake.queue(MANAGER, intake(), check("revise", ["漏了端点 x=2 的函数值"]))
    t.fake.queue("b2", f"初稿。{REASONING}", f"改后的版本。{REASONING}")
    await run(t, "intake", "handle", "check")
    o = t.ctx.state.office
    assert o.check["verdict"] == "revise"
    assert o.fixed.startswith("改后的版本")
    assert final_answer(t.ctx.state) == o.fixed
    fix_prompt = t.prompts_for("b2")[1]
    assert "漏了端点 x=2 的函数值" in fix_prompt and "初稿" in fix_prompt
    assert [s for s, _ in recorded] == ["handle", "fix"]
    # 只退回一次：经理不再过目第二遍
    assert len(t.calls_for(MANAGER)) == 2


async def test_revise_without_issues_counts_as_pass():
    t = office_table()
    t.fake.queue(MANAGER, intake(), check("revise", []))
    await run(t, "intake", "handle", "check")
    o = t.ctx.state.office
    assert o.check["verdict"] == "pass" and o.fixed is None
    assert len(t.calls_for("b2")) == 1


async def test_unusable_intake_falls_back_to_cheapest_matching_staff():
    t = office_table()
    t.fake.queue(MANAGER, *["派给谁都行"] * 4)
    (res,) = await run(t, "intake")
    o = t.ctx.state.office
    assert o.intake["by_rule"] and o.intake["mode"] == "single"
    assert o.intake["assignee"] == "甲"  # b1 最便宜
    assert res.notes and "按规则派活" in res.notes[0]


async def test_unknown_assignee_falls_back_to_rule():
    t = office_table()
    t.fake.queue(MANAGER, *[intake(assignee="组员戊")] * 4)
    await run(t, "intake")
    assert t.ctx.state.office.intake["by_rule"]


async def test_image_question_goes_to_a_vision_staff():
    t = office_table()
    t.ctx.attachments = (
        Attachment("a0", "p.png", "image", "image/png", "png", len(PNG), "k", "ready", data=PNG),
    )
    t.fake.queue(MANAGER, intake(assignee="组员甲"))  # 甲看不了图
    await run(t, "intake")
    o = t.ctx.state.office
    assert o.intake["assignee"] == "乙" and o.intake["by_rule"]


async def test_meeting_suggestion_is_handled_as_single_with_a_note():
    t = office_table()
    t.fake.queue(MANAGER, intake(mode="meeting"))
    (res,) = await run(t, "intake")
    assert t.ctx.state.office.intake["mode"] == "meeting"
    assert any("按单人办处理" in n for n in res.notes)


async def test_failed_staff_is_reassigned_once():
    t = office_table()
    t.fake.queue(MANAGER, intake(assignee="组员丙"))
    t.fake.queue("b3", *[ErrorKind.SERVER] * 4)
    _, res = await run(t, "intake", "handle")
    o = t.ctx.state.office
    assert o.worker == "甲" and o.work
    assert "改派" in res.notes[0]


async def test_manager_check_failure_delivers_work_with_note():
    t = office_table()
    t.fake.queue(MANAGER, intake(), *["看过了"] * 4)
    results = await run(t, "intake", "handle", "check")
    o = t.ctx.state.office
    assert o.check["degraded"] and o.fixed is None
    assert final_answer(t.ctx.state) == o.work
    assert "直接交付" in results[2].notes[0]


async def test_office_needs_a_manager():
    t = Table(coordinator=None)
    with pytest.raises(StepFailed, match="经理"):
        await run(t, "intake")


async def test_restore_office_roundtrip():
    t = office_table()
    t.fake.queue(MANAGER, intake(), check("revise", ["补充单调性"]))
    await run(t, "intake", "handle", "check")
    restored = restore_state(t.repo, t.session, 0).office
    o = t.ctx.state.office
    assert restored.intake == o.intake and restored.check == o.check
    assert (restored.work, restored.worker, restored.fixed) == (o.work, o.worker, o.fixed)


async def test_rerun_after_restore_makes_no_new_calls():
    t = office_table()
    t.fake.queue(MANAGER, intake(), check())
    await run(t, "intake", "handle", "check")
    before = len(t.fake.calls)
    t.ctx.state = restore_state(t.repo, t.session, 0)
    await run(t, "intake", "handle", "check")
    assert len(t.fake.calls) == before
