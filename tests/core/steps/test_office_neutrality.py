"""办公室模式的无品牌偏好：把所有厂商和 id 换名后，同一 seed 下开会的参会者、项目组、
复核经理和预算单在位置上完全一致。"""

from __future__ import annotations

import contextlib
import random

from roundtable.core.providers import ChannelRouter
from roundtable.core.steps import NeedsApproval, get_step
from roundtable.core.steps.office import pick_reviewer

from ..routing.conftest import POOL, app_config
from .conftest import MEMBERS, Table
from .test_office import MANAGER, intake

# id 倒过来编号（按 id 排序会得到相反的顺序），厂商也换名
RENAME = {mid: f"x{len(POOL) - i}" for i, (mid, *_) in enumerate(POOL)}
RENAMED_POOL = [
    (RENAME[mid], f"Brand{9 - int(vendor[1])}", tier, tags, pin, pout)
    for mid, vendor, tier, tags, pin, pout in POOL
]
MANAGERS = ("f1", "f2", "f3")


def tables(seed: int) -> tuple[Table, Table]:
    """同一 seed 的两张办公室桌子：原名的，和所有 id、厂商换名的。"""
    a = Table(seed, coordinator=MANAGER)
    a.config = a.ctx.config = app_config(managers=MANAGERS)
    b = Table(seed, members={c: RENAME[m] for c, m in MEMBERS.items()}, coordinator=RENAME[MANAGER])
    b.config = b.ctx.config = app_config(
        pool=RENAMED_POOL, managers=tuple(RENAME[m] for m in MANAGERS)
    )
    for t in (a, b):
        policy = t.config.roundtable.request.model_copy(update={"backoff_s": 0})
        t.router = t.ctx.router = ChannelRouter(t.config.models, {"c": t.fake}, policy=policy)
    return a, b


async def triage(t: Table, manager: str, reply: str) -> dict:
    t.fake.queue(manager, reply)
    with contextlib.suppress(NeedsApproval):  # 超过上限时会弹卡片；这里只比较分诊结果
        await get_step("intake").run(t.ctx)
    return dict(t.ctx.state.office.intake or {})


async def test_meeting_attendees_and_budget_do_not_depend_on_names():
    for seed in range(5):
        a, b = tables(seed)
        # 经理只点了一位：其余按规则补齐
        reply = intake(mode="meeting", attendees=("组员乙",))
        x = await triage(a, MANAGER, reply)
        y = await triage(b, RENAME[MANAGER], reply)
        assert x["attendees"] == y["attendees"] and len(x["attendees"]) >= 2
        assert x["budget_usd"] == y["budget_usd"]


async def test_project_team_and_budget_do_not_depend_on_names():
    for seed in range(5):
        a, b = tables(seed)
        reply = intake(mode="project", attendees=())
        x = await triage(a, MANAGER, reply)
        y = await triage(b, RENAME[MANAGER], reply)
        assert x["attendees"] == y["attendees"] and len(x["attendees"]) >= 2
        assert x["budget_usd"] == y["budget_usd"]


def test_reviewer_choice_does_not_depend_on_names():
    seen = set()
    for seed in range(20):
        a, b = tables(seed)
        a.ctx.rng, b.ctx.rng = random.Random(seed), random.Random(seed)
        x, y = pick_reviewer(a.ctx), pick_reviewer(b.ctx)
        assert x is not None and RENAME[x] == y
        seen.add(x)
    assert seen == {"f2", "f3"}  # 主办经理以外的经理都有机会


def test_group_estimates_pick_the_cheapest_staff_and_ignore_names():
    from roundtable.core.routing import Lineup, Question
    from roundtable.core.steps.office import estimate_meeting, group_estimates

    a, b = tables(0)
    staff = ["b3", "b2", "b1"]  # 工资从高到低
    lineup = Lineup(tuple(staff), MANAGER, ())
    renamed = Lineup(tuple(RENAME[m] for m in staff), RENAME[MANAGER], ())
    q = Question("写一篇关于秋天的短文")
    x = group_estimates(a.config, a.router, q, lineup)
    y = group_estimates(b.config, b.router, q, renamed)
    assert x == y
    # 最少人数时按工资最低的两位估
    assert x["meeting"]["usd"][0] == estimate_meeting(a.config, a.router, q, ["b1", "b2"], MANAGER)
    assert x["meeting"]["people"] == [2, 3] and x["project"]["people"] == [2, 3]
