"""绩效（O5）：从办公室模式的记录推算件数、被退回、通过率、平均工资与用时；可清零。"""

from __future__ import annotations

import random

from roundtable.core.office import collect, reset
from roundtable.core.office.performance import Performance, Stats, brief
from roundtable.core.service import RoundtableService
from roundtable.core.steps.office import _best_performers

from ..orchestrator.conftest import MEDIUM, Env
from ..steps.test_office import office_table


def make() -> tuple[Env, RoundtableService]:
    env = Env(confirm_threshold_usd=100.0)
    return env, RoundtableService(env.rt)


async def office_run(svc, env, mode="single", check="pass", seed=3, **kw) -> str:
    env.office_mode, env.office_check = mode, check
    sid = svc.create(MEDIUM, workflow="office", seed=seed, **kw)
    await svc.wait(sid)
    assert svc.session(sid)["status"] == "completed"
    return sid


def worker_model(env, sid) -> str:
    from roundtable.core.steps import restore_state

    o = restore_state(env.rt.repo, sid, 0).office
    return env.rt.repo.tables(sid)[0]["members"][o.worker]


async def test_single_jobs_count_checks_and_returns():
    env, svc = make()
    a = await office_run(svc, env, check="pass", seed=3)
    b = await office_run(svc, env, check="revise", seed=3)
    model = worker_model(env, a)
    assert worker_model(env, b) == model
    perf = collect(env.rt.repo)
    s = perf.by_model[model]["single"]
    assert (s.jobs, s.checked, s.returned) == (2, 2, 1)
    assert s.pass_rate == 0.5
    assert s.cost_usd > 0
    total = perf.to_dict()["by_model"][model]["total"]
    assert total["jobs"] == 2 and abs(total["avg_cost_usd"] - s.cost_usd / 2) < 1e-12


async def test_meeting_and_project_jobs():
    env, svc = make()
    await office_run(svc, env, mode="meeting")
    await office_run(svc, env, mode="project")
    perf = collect(env.rt.repo)
    meetings = [k["meeting"] for k in perf.by_model.values() if "meeting" in k]
    assert len(meetings) == 2 and all(m.jobs == 1 and m.checked == 0 for m in meetings)
    projects = [k["project"] for k in perf.by_model.values() if "project" in k]
    assert projects and sum(p.jobs for p in projects) >= 2
    assert all(p.checked <= p.jobs for p in projects)


async def test_reset_starts_over_and_anonymous_sessions_are_left_out():
    env, svc = make()
    await office_run(svc, env)
    assert collect(env.rt.repo).by_model
    reset(env.rt.repo, env.rt.repo.clock())
    assert not collect(env.rt.repo).by_model
    sid = await office_run(svc, env, anonymous=True)
    assert not collect(env.rt.repo).by_model  # 匿名未揭晓不计入
    svc.reveal_identities(sid)
    assert collect(env.rt.repo).by_model


async def test_service_office_includes_performance_and_reset():
    env, svc = make()
    await office_run(svc, env)
    data = svc.office()
    rows = [e for e in data["staff"] if e["performance"]]
    assert rows and rows[0]["performance"]["single"]["jobs"] == 1
    assert data["performance_since"] is None and data["performance_min_jobs"] >= 1
    after = svc.reset_performance()
    assert after["performance_since"] and not any(e["performance"] for e in after["staff"])


def test_brief_only_mentions_kinds_with_enough_jobs():
    perf = Performance()
    perf.by_model["m"] = {"single": Stats(4, 4, 1), "meeting": Stats(1)}
    assert brief(perf, "m", 3) == "单人办 4 件，通过率 75%"
    assert brief(perf, "m", 1) == "单人办 4 件，通过率 75%；开会 1 件"
    assert brief(perf, "x", 1) == ""


async def test_staff_list_shows_performance_to_the_manager():
    t = office_table()
    perf = Performance()
    perf.by_model["b2"] = {"single": Stats(5, 5, 1)}
    t.ctx.state.office.performance = perf
    from roundtable.core.steps.office import staff_block

    block = staff_block(t.ctx)
    line = next(x for x in block.splitlines() if "组员乙" in x)
    assert "绩效：单人办 5 件，通过率 80%" in line
    assert "绩效" not in next(x for x in block.splitlines() if "组员甲" in x)


def test_rule_pick_prefers_better_record_only_among_equal_wages():
    t = office_table()
    b1 = t.ctx.models_by_id["b1"]
    twin = b1.model_copy(update={"id": "b1x"})
    pricier = t.ctx.models_by_id["b3"]
    perf = Performance()
    perf.by_model["b1"] = {"single": Stats(5, 5, 4)}  # 通过率 20%
    perf.by_model["b1x"] = {"single": Stats(5, 5, 0)}  # 通过率 100%
    perf.by_model["b3"] = {"single": Stats(9, 9, 0)}
    t.ctx.state.office.performance = perf
    t.ctx.rng = random.Random(0)
    best = _best_performers(t.ctx, [b1, twin, pricier])
    # 只在工资相同的里挑；更贵的员工绩效再好也不选
    assert [m.id for m in best] == ["b1x"]
    # 记录太少（少于 min_jobs）不参考
    perf.by_model["b1"] = {"single": Stats(1, 1, 1)}
    perf.by_model["b1x"] = {"single": Stats(1, 1, 0)}
    assert {m.id for m in _best_performers(t.ctx, [b1, twin])} == {"b1", "b1x"}
