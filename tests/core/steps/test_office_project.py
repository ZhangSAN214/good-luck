"""办公室模式·项目分工（O4）的分诊与复核经理验收（单桌层面）。"""

from __future__ import annotations

import pytest

from roundtable.core.steps import NeedsApproval, StepFailed, get_step
from roundtable.core.steps.office import pick_reviewer, project_config

from .conftest import Table
from .test_office import MANAGER, intake, office_table, run
from .test_office_meeting import MINUTES


def project(*names: str) -> str:
    return intake(assignee="组员甲", mode="project", attendees=names, brief="写一份完整报告")


async def test_project_team_is_named_and_handle_check_are_skipped():
    t = office_table()
    t.fake.queue(MANAGER, project("组员乙", "组员丙"))
    results = await run(t, "intake", "handle", "check")
    o = t.ctx.state.office
    assert o.project and o.intake["attendees"] == ["乙", "丙"] and o.intake["approved"]
    assert o.intake["project_estimate_usd"] > 0
    assert results[1].calls == 0 and results[2].calls == 0
    assert o.final is None  # 成果在项目组桌子上
    assert len(t.fake.calls) == 1


async def test_project_team_capped_and_filled():
    t = Table(
        coordinator=MANAGER, members={"甲": "b1", "乙": "b2", "丙": "b3", "丁": "f2", "戊": "f3"}
    )
    t.fake.queue(MANAGER, project("组员甲", "组员乙", "组员丙", "组员丁", "组员戊"))
    await run(t, "intake")
    assert t.ctx.state.office.intake["attendees"] == ["甲", "乙", "丙", "丁"]
    t = office_table()
    t.fake.queue(MANAGER, project("组员丙"))
    (res,) = await run(t, "intake")
    assert t.ctx.state.office.intake["attendees"] == ["丙", "甲"]
    assert any("按规则补齐" in n for n in res.notes)


async def test_project_needs_two_staff():
    t = Table(coordinator=MANAGER, members={"甲": "b1"})
    t.fake.queue(MANAGER, project("组员甲"))
    (res,) = await run(t, "intake")
    assert t.ctx.state.office.intake["mode"] == "single"
    assert any("没法项目分工" in n for n in res.notes)


async def test_project_over_cap_card():
    t = office_table()
    t.ctx.config = t.config.model_copy(deep=True)
    object.__setattr__(t.ctx.config.roundtable.office.task_caps_usd, "project", 0.000001)
    t.fake.queue(MANAGER, project("组员乙", "组员丙"))
    with pytest.raises(NeedsApproval) as info:
        await run(t, "intake")
    card = info.value.card
    assert card.option_keys() == ["project", "single", "stop"] and card.recommendation == "project"


def test_project_config_applies_reviews_and_step_overrides():
    t = office_table()
    cfg = project_config(t.config)
    p = t.config.roundtable.office.project
    assert cfg.roundtable.reviews_per_answer == p.reviews_per_item
    for step, override in p.step_params.items():
        for k, v in override.items():
            assert cfg.roundtable.step_params[step][k] == v


async def test_step_overrides_reach_the_call():
    t = office_table()
    t.ctx.step_overrides = {"handle": {"reasoning": {"effort": "medium"}}}
    assert t.ctx.step_params("handle")["reasoning"] == {"effort": "medium"}
    assert t.ctx.step_params("handle")["max_tokens"] == 6000


async def test_reviewer_is_the_other_manager():
    t = office_table()
    reviewer = pick_reviewer(t.ctx)
    assert reviewer is not None and reviewer != MANAGER
    managers = {m.id for m in t.config.models.models if m.office_role == "manager"}
    assert reviewer in managers


async def test_office_review_without_merge_fails():
    t = office_table()
    with pytest.raises(StepFailed, match="合并"):
        await get_step("office_review").run(t.ctx)


async def test_office_review_skips_without_a_second_manager(monkeypatch):
    from roundtable.core.steps import office as office_mod
    from roundtable.core.steps.collab_schemas import Merge

    t = office_table()
    t.ctx.state.collab.merge = Merge.from_dict({"result": MINUTES, "confidence": "high"})
    monkeypatch.setattr(office_mod, "pick_reviewer", lambda ctx: None)
    (res,) = await run(t, "office_review")
    assert t.ctx.state.office.check["skipped"]
    assert "没有第二位经理" in res.notes[0]
    assert not t.fake.calls


async def test_budget_sheet_for_every_mode():
    """经理分诊后附预算单：按选定的方式和人选估计；开会、项目分工比单人办贵。"""
    from .test_office_meeting import meeting

    budgets = {}
    for mode, reply in (
        ("single", intake()),
        ("meeting", meeting("组员甲", "组员丙")),
        ("project", project("组员甲", "组员丙")),
    ):
        t = office_table()
        t.fake.queue(MANAGER, reply)
        await run(t, "intake")
        budgets[mode] = t.ctx.state.office.intake["budget_usd"]
    assert all(v > 0 for v in budgets.values())
    assert budgets["project"] > budgets["single"]
