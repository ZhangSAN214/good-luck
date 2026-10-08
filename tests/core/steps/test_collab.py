"""协同模式步骤：拆分 → 自荐 → 分配 → 完成 → 交叉审查 → 修改 → 合并（Fake 模型，单桌）。"""

from __future__ import annotations

import json
import re

import pytest

from roundtable.core.steps import get_step, outcome_signals, restore_state, table_contributions
from roundtable.core.steps.collab import cross_review_plan

from .conftest import (
    COORDINATOR,
    MEMBERS,
    REASONING,
    Table,
    decompose_reply,
    default_reply,
)

STEPS = ("decompose", "volunteer", "assign", "work", "cross_review", "rework", "merge")
BIG = {"甲": "b1", "乙": "b2", "丙": "b3", "丁": "f2", "戊": "f3"}


async def run(table: Table, *steps: str):
    return [await get_step(s).run(table.ctx) for s in steps]


def with_decompose(table: Table, n: int, depends: bool = False) -> None:
    def reply(model, messages):
        if "把任务拆成子任务" in messages[0].content and "学习小组的统筹" in messages[0].content:
            return decompose_reply(n, depends)(model, messages)
        return default_reply(model, messages)

    table.fake._default = reply


def calls(table: Table, marker: str):
    return [c for c in table.fake.calls if marker in c.messages[0].content]


async def test_full_collab_table():
    t = Table(1)
    await run(t, *STEPS)
    c = t.ctx.state.collab
    assert [s.id for s in c.subtasks] == ["T1", "T2"]
    assert set(c.volunteers) == set(MEMBERS)
    load = c.assignment.load()
    assert set(load) == set(MEMBERS) and all(n >= 1 for n in load.values())  # 每人至少一块
    assert all(c.assignment.owners[s] for s in ("T1", "T2"))  # 每块至少一人
    assert set(c.works) == {
        (sid, code) for sid, owners in c.assignment.owners.items() for code in owners
    }
    assert c.merge.output.result.startswith("完整成果")
    assert outcome_signals(t.ctx.state).confidence == "high"
    # 协同步骤只由统筹做拆分、分配、合并
    coordinator_steps = {c.model for c in calls(t, "学习小组的统筹")}
    assert coordinator_steps == {COORDINATOR}


async def test_member_prompts_identical_except_code():
    t = Table(2)
    await run(t, "decompose", "volunteer")
    prompts = {
        m: (c.messages[0].content.replace(f"组员{code}", "X"), c.messages[1].content)
        for code, m in MEMBERS.items()
        for c in t.calls_for(m)
    }
    assert len(set(prompts.values())) == 1


async def test_more_members_than_subtasks_share_blocks():
    t = Table(3, members=BIG)
    with_decompose(t, 2)
    await run(t, "decompose", "volunteer", "assign", "work")
    c = t.ctx.state.collab
    assert sum(len(v) for v in c.assignment.owners.values()) == 5
    assert len(c.works) == 5  # 同一块的多位负责人各自独立完成
    work_calls = calls(t, "你负责下面 <your_subtask>")
    for call in work_calls:  # 看不到同一块其他人的版本
        assert "<work" not in call.messages[1].content


async def test_more_subtasks_than_members():
    t = Table(4)
    with_decompose(t, 5)
    await run(t, "decompose", "volunteer", "assign")
    load = t.ctx.state.collab.assignment.load()
    assert sum(load.values()) == 5 and max(load.values()) - min(load.values()) <= 1


async def test_dependencies_run_in_order_and_are_passed_on():
    t = Table(5)
    with_decompose(t, 2, depends=True)
    await run(t, "decompose", "volunteer", "assign", "work")
    work_calls = calls(t, "你负责下面 <your_subtask>")
    first_t2 = next(
        i for i, c in enumerate(work_calls) if 'your_subtask id="T2"' in c.messages[1].content
    )
    t1_calls = [
        i for i, c in enumerate(work_calls) if 'your_subtask id="T1"' in c.messages[1].content
    ]
    assert max(t1_calls) < first_t2  # T1 全部完成后才开始 T2
    t2_prompt = work_calls[first_t2].messages[1].content
    assert '<dependency subtask="T1"' in t2_prompt and "推导" in t2_prompt


@pytest.mark.parametrize("seed", range(15))
async def test_cross_review_never_own_work_and_balanced(seed):
    t = Table(seed, members=BIG)
    with_decompose(t, 3)
    await run(t, "decompose", "volunteer", "assign", "work")
    items = t.ctx.state.collab.items()
    plan = cross_review_plan(t.ctx, list(BIG))
    received: dict[str, int] = {}
    for reviewer, targets in plan.items():
        for item in targets:
            assert items[item][1] != reviewer  # 永远不审自己的
            received[item] = received.get(item, 0) + 1
    k = t.config.roundtable.reviews_per_answer
    assert set(received) == set(items) and set(received.values()) == {k}
    # 有可选的外人时，不让同一子任务的其他负责人互审
    owners = t.ctx.state.collab.assignment.owners
    for reviewer, targets in plan.items():
        for item in targets:
            sid = items[item][0]
            outsiders = [r for r in BIG if r not in owners[sid]]
            if len(outsiders) >= k:
                assert reviewer not in owners[sid]


@pytest.mark.parametrize("seed", range(15))
async def test_cross_review_balanced_when_each_owns_one_block(seed):
    t = Table(seed, members=BIG)
    with_decompose(t, 5)
    await run(t, "decompose", "volunteer", "assign", "work")
    plan = cross_review_plan(t.ctx, list(BIG))
    loads = [len(v) for v in plan.values()]
    assert max(loads) - min(loads) <= 1


async def test_cross_review_and_rework_flow():
    t = Table(6)
    await run(t, *STEPS[:6])
    c = t.ctx.state.collab
    items = c.items()
    for reviewer, reviews in c.cross_reviews.items():
        assert reviews and all(items[r.target][1] != reviewer for r in reviews)
    for key, revision in c.reworks.items():
        assert not revision.skipped and revision.decisions
        prompt = next(
            call.messages[1].content
            for call in calls(t, "负责的子任务已经由其他组员审查")
            if call.model == MEMBERS[key[1]]
            and f'<your_subtask id="{key[0]}"' in call.messages[1].content
        )
        assert f'from="组员{key[1]}"' not in prompt  # 收不到自己的审查


async def test_bad_decomposition_falls_back_to_single_subtask():
    t = Table(7)

    def reply(model, messages):
        if "把任务拆成子任务" in messages[0].content and "学习小组的统筹" in messages[0].content:
            return "不是 JSON"
        return default_reply(model, messages)

    t.fake._default = reply
    [result] = await run(t, "decompose")
    c = t.ctx.state.collab
    assert [s.id for s in c.subtasks] == ["T1"] and c.subtasks_degraded
    assert result.degraded == ("coordinator",) and result.notes


async def test_invalid_assignment_retried_then_repaired():
    t = Table(8)

    def reply(model, messages):
        if "负责把子任务分配给组员" in messages[0].content:
            return json.dumps({"assignments": [{"subtask": "T1", "members": ["组员甲"]}]})
        return default_reply(model, messages)

    t.fake._default = reply
    [_, _, result] = await run(t, "decompose", "volunteer", "assign")
    assert len(calls(t, "负责把子任务分配给组员")) == 2  # 重新分配一次
    a = t.ctx.state.collab.assignment
    assert a.repaired and result.notes
    assert set(a.load()) == set(MEMBERS) and all(a.owners.values())


async def test_lazy_work_redone_and_flagged_for_merge():
    t = Table(9)

    def reply(model, messages):
        if model == "b1" and "你负责下面 <your_subtask>" in messages[0].content:
            return "略"
        return default_reply(model, messages)

    t.fake._default = reply
    await run(t, *STEPS)
    flagged = [r for r in t.ctx.state.effort.values() if r.step == "work"]
    assert flagged and all(r.lazy and r.item for r in flagged)
    merge_prompt = calls(t, "合并成一份完整成果")[-1].messages[1].content
    assert re.search(r'from="组员甲" flagged=', merge_prompt)


async def test_lazy_volunteer_redone():
    t = Table(10)

    def reply(model, messages):
        if model == "b2" and "现在请你自荐" in messages[0].content and len(messages) == 2:
            return json.dumps({"strengths": "", "preferences": []})
        return default_reply(model, messages)

    t.fake._default = reply
    await run(t, "decompose", "volunteer")
    record = t.ctx.state.effort[("volunteer", "乙", "")]
    assert record.status == "redone"
    assert t.ctx.state.collab.volunteers["乙"].strengths


async def test_merge_failure_falls_back_with_low_confidence():
    t = Table(11)

    def reply(model, messages):
        if "合并成一份完整成果" in messages[0].content:
            return "合并不了"
        return default_reply(model, messages)

    t.fake._default = reply
    [*_, result] = await run(t, *STEPS)
    merge = t.ctx.state.collab.merge
    assert merge.degraded and "修订说明" in merge.output.result and "组员" in merge.output.result
    assert outcome_signals(t.ctx.state).confidence == "low"  # 会询问是否升级
    assert result.degraded == ("coordinator",)


async def test_collab_state_restored_and_steps_not_repeated():
    t = Table(12)
    await run(t, *STEPS[:4])
    restored = restore_state(t.repo, t.session, 0)
    c, r = t.ctx.state.collab, restored.collab
    assert r.subtasks == c.subtasks and r.volunteers == c.volunteers
    assert r.assignment == c.assignment and r.works == c.works
    before = len(t.fake.calls)
    t.ctx.state = restored
    await run(t, *STEPS[:4])  # 已完成的步骤不重复调用
    assert len(t.fake.calls) == before
    await run(t, *STEPS[4:])
    restored = restore_state(t.repo, t.session, 0)
    assert restored.collab.cross_reviews == t.ctx.state.collab.cross_reviews
    assert restored.collab.reworks == t.ctx.state.collab.reworks
    assert restored.collab.merge == t.ctx.state.collab.merge


async def test_collab_contributions():
    t = Table(13)
    await run(t, *STEPS)
    counts = table_contributions(t.ctx.state, list(MEMBERS))
    for code in MEMBERS:
        assert counts[code]["answered"] >= 1 and counts[code]["adopted"] >= 1
        assert counts[code]["valid_review"] >= 1 and counts[code]["issue_accepted"] >= 1
    assert sum(counts[c]["volunteer_accepted"] for c in MEMBERS) >= 1


def test_reasoning_fixture_is_substantive():
    assert len(REASONING) > 150
