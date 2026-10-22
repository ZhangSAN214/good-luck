"""办公室模式·项目分工（O4）：经理点名项目组 → 另开一张项目组桌子走协同流水线 → 另一位经理验收。"""

from __future__ import annotations

from roundtable.core.config.schema import OfficeTaskCaps
from roundtable.core.service import RoundtableService
from roundtable.core.steps import final_answer, restore_state

from .conftest import MEDIUM, Env


def make(project_cap: float = 100.0, review: str = "pass", **kw) -> tuple[Env, RoundtableService]:
    office = Env().config.roundtable.office.model_copy(
        update={"task_caps_usd": OfficeTaskCaps(single=100.0, project=project_cap)}
    )
    env = Env(confirm_threshold_usd=100.0, rt_update={"office": office}, **kw)
    env.office_mode = "project"
    env.office_review = review
    return env, RoundtableService(env.rt)


def table_calls(env: Env, sid: str, table_no: int):
    return [c for c in env.rt.repo.session_view(sid).calls if c.table_no == table_no]


async def test_project_runs_on_a_second_table_with_only_the_team():
    env, svc = make()
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "completed", data.get("error")
    tables = env.rt.repo.tables(sid)
    assert len(tables) == 2
    first, project = tables
    # 项目组只有经理点名的两位，主办经理统筹（与分诊的经理相同）
    assert len(project["members"]) == 2 and project["coordinator"] == first["coordinator"]
    team = {
        first["members"][c] for c in restore_state(env.rt.repo, sid, 0).office.intake["attendees"]
    }
    assert set(project["members"].values()) == team
    assert "office_review" in project["pipeline"]
    # 第一张桌子只有分诊；办理、过目跳过
    assert {c.step for c in table_calls(env, sid, 0)} == {"intake"}
    # 项目组桌子：没点名的员工一次都没被调用
    called = {c.model_id for c in table_calls(env, sid, 1) if c.role == "member"}
    assert called <= team
    # 复核经理是另一位经理
    reviews = [c for c in table_calls(env, sid, 1) if c.step == "office_review"]
    assert len(reviews) == 1 and reviews[0].model_id != first["coordinator"]
    assert reviews[0].role == "reviewer"
    state = restore_state(env.rt.repo, sid, 1)
    assert final_answer(state) == state.collab.merge.output.result
    # 项目组桌子不算升级
    assert data["routing"]["escalated"] is False


async def test_project_cross_review_uses_one_reviewer_and_medium_merge():
    env, svc = make()
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    calls = table_calls(env, sid, 1)
    works = [c for c in calls if c.step == "work" and c.error is None]
    reviews = [c for c in calls if c.step == "cross_review"]
    # 每份成果只找一位审查者：审查调用不多于成果份数
    assert reviews and len(reviews) <= len(works)
    merge = [c for c in env.fake.calls if "合并成一份完整成果" in c.messages[0].content]
    assert merge and merge[-1].params.get("reasoning") == {"effort": "medium"}


async def test_reviewer_sends_back_once_and_revision_is_delivered():
    env, svc = make(review="revise")
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    assert svc.session(sid)["status"] == "completed"
    state = restore_state(env.rt.repo, sid, 1)
    assert state.office.check["verdict"] == "revise"
    assert final_answer(state).startswith("修改后的项目成果")
    revise = [c for c in table_calls(env, sid, 1) if c.step == "office_revise"]
    assert len(revise) == 1 and revise[0].role == "coordinator"


async def test_project_over_cap_asks_and_single_is_possible():
    env, svc = make(project_cap=0.000001)
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    data = svc.session(sid)
    card = data["pending_checkpoint"]
    assert card["kind"] == "office"
    assert [o["key"] for o in card["card"]["options"]] == ["project", "single", "stop"]
    assert "项目组" in card["card"]["situation"]
    svc.respond(sid, "single")
    await svc.wait(sid)
    assert svc.session(sid)["status"] == "completed"
    assert len(env.rt.repo.tables(sid)) == 1
    office = restore_state(env.rt.repo, sid, 0).office
    assert office.intake["mode"] == "single" and office.intake["declined"] == "project"
    assert office.work


async def test_project_over_cap_approved_goes_ahead():
    env, svc = make(project_cap=0.000001)
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    svc.respond(sid, "project")
    await svc.wait(sid)
    assert svc.session(sid)["status"] == "completed"
    assert len(env.rt.repo.tables(sid)) == 2


async def test_payslip_has_a_reviewer_line():
    env, svc = make()
    sid = svc.create(MEDIUM, workflow="office", seed=3)
    await svc.wait(sid)
    slip = svc.payslip(sid)
    assert any(line["who"] == "复核经理" for line in slip["lines"])
    assert abs(slip["total_usd"] - svc.session(sid)["cost_usd"]) < 1e-9
