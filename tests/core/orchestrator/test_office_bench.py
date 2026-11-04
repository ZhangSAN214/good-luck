"""办公室交接（M）：经理决定项目分工时先问老板要不要交给工作台；同意后开项目室（办公室里说清楚的
不再问），工作台做完把成品和验收结果回报到办公室那场对话。只用假助手，不真跑生成器。"""

from __future__ import annotations

import json

from roundtable.core.benchkit.builder import BenchBuilder
from roundtable.core.config.schema import BenchRules, OfficeTaskCaps
from roundtable.core.service import RoundtableService
from roundtable.core.steps import final_answer, restore_state
from roundtable.core.steps.office import BENCH_FINAL
from tests.core.benchkit.test_builder import ProjectSC, fake_check, render

from .conftest import Env

ASK = "帮我做一个给家人用的旅行规划台，要能看地图"


def make(tmp_path, ready=True):
    office = Env().config.roundtable.office.model_copy(
        update={"task_caps_usd": OfficeTaskCaps(single=100.0, project=100.0)}
    )
    env = Env(confirm_threshold_usd=100.0, rt_update={"office": office})
    env.office_mode = "project"
    svc = RoundtableService(env.rt)
    sc = ProjectSC()
    svc.bench = BenchBuilder(
        BenchRules(project={"enabled": True}, hero={"enabled": False}),
        root=tmp_path,
        supercomputer=sc,
        render=render,
        check=fake_check(),
        seed=7,
        report=svc._bench_report,
    )
    svc.orc.bench_ready = lambda: ready
    return env, svc, sc


def intake_prompts(env):
    return [
        c.messages
        for c in env.fake.calls
        if "办公室的经理" in c.messages[0].content and "派给合适的员工" in c.messages[0].content
    ]


async def test_big_project_asks_hands_to_bench_and_reports_back(tmp_path):
    env, svc, sc = make(tmp_path)
    sid = svc.create(ASK, workflow="office", seed=3)
    await svc.wait(sid)
    card = svc.session(sid)["pending_checkpoint"]
    assert card["kind"] == "office"
    assert [o["key"] for o in card["card"]["options"]] == ["bench", "office", "stop"]
    assert card["card"]["recommendation"] == "bench"
    assert "交给工作台" in card["card"]["situation"] and "不再问" in card["card"]["situation"]

    svc.respond(sid, "bench")
    await svc.wait(sid)
    data = svc.session(sid)
    assert data["status"] == "completed"
    assert len(env.rt.repo.tables(sid)) == 1  # 不开项目组桌子、不派活
    calls = env.rt.repo.session_view(sid).calls
    assert {c.step for c in calls} == {"intake"}
    o = restore_state(env.rt.repo, sid, 0).office
    assert o.intake["mode"] == "bench" and final_answer(restore_state(env.rt.repo, sid, 0)) == (
        BENCH_FINAL
    )
    started = [x for x in env.rt.repo.outputs(sid) if x["kind"] == "office_bench"]
    content = json.loads(started[0]["content"])
    assert content["started"] and content["bench_id"]

    # 工作台：办公室里说清楚的写进需求书；访谈里「给谁用」（选项「家人」已经说了）不再问
    await svc.bench.wait()
    b = svc.bench.build
    assert b["id"] == content["bench_id"] and b["handoff"]["session_id"] == sid
    assert "办公室里已经和用户说清楚的" in b["brief"] and ASK in b["brief"]
    interview = next(
        json.loads(p) for p in sc.prompts if json.loads(p)["role"] == "bench_interview"
    )
    assert ASK in interview["answered"] and "写清推导" in interview["answered"]  # 经理的工作说明
    assert [q["q"] for q in b["interview"]["questions"]] == ["对标哪个产品？"]
    assert any("不再问" in x["text"] for x in b["log"])
    assert any(v["src"] == "办公室" for v in b["voices"])

    # 跑完项目室：签字 → 演示通过 → 验收通过 → 回报到办公室那场对话
    svc.bench.answer(b["id"], {})
    await svc.bench.wait()
    svc.bench.sign(b["id"])
    await svc.bench.wait()
    while b["gate"]["kind"] != "accept":
        svc.bench.review(b["id"], "pass")
        await svc.bench.wait()
    assert not [x for x in env.rt.repo.outputs(sid) if x["kind"] == "office_bench_report"]
    svc.bench.review(b["id"], "pass")
    reports = [x for x in env.rt.repo.outputs(sid) if x["kind"] == "office_bench_report"]
    assert len(reports) == 1
    rep = json.loads(reports[0]["content"])
    assert rep["status"] == "delivered" and rep["outcome"] == "你验收通过，已交付"
    assert rep["url"] == f"/bench/{b['id']}/index.html" and rep["total"] == rep["met"] > 0
    assert restore_state(env.rt.repo, sid, 0).office.bench_report["bench_id"] == b["id"]
    assert b["handoff"]["reported"]
    # 只回报一次
    svc.bench._report_back(b)
    assert len([x for x in env.rt.repo.outputs(sid) if x["kind"] == "office_bench_report"]) == 1
    # 再触发一次「完成」也不会重复交
    svc._bench_handoff(sid, "completed")
    assert len([x for x in env.rt.repo.outputs(sid) if x["kind"] == "office_bench"]) == 1


async def test_office_keeps_it_or_bench_not_ready_asks_nothing(tmp_path):
    env, svc, _ = make(tmp_path)
    sid = svc.create(ASK, workflow="office", seed=3)
    await svc.wait(sid)
    svc.respond(sid, "office")  # 办公室自己做：照原来的项目分工
    await svc.wait(sid)
    assert svc.session(sid)["status"] == "completed"
    assert len(env.rt.repo.tables(sid)) == 2 and svc.bench.build is None

    env2, svc2, _ = make(tmp_path / "busy", ready=False)
    sid2 = svc2.create(ASK, workflow="office", seed=3)
    await svc2.wait(sid2)
    assert svc2.session(sid2)["status"] == "completed"  # 工作台不能接活：不问
    assert len(env2.rt.repo.tables(sid2)) == 2
    # 中立：问不问工作台，经理分诊收到的提示词一字不差（办公室的提示词不受影响）
    assert intake_prompts(env) == intake_prompts(env2)


async def test_bench_cannot_start_writes_the_reason_into_the_conversation(tmp_path):
    env, svc, _ = make(tmp_path)
    svc.bench.rules = svc.bench.rules.model_copy(update={"enabled": False})
    sid = svc.create(ASK, workflow="office", seed=3)
    await svc.wait(sid)
    svc.respond(sid, "bench")
    await svc.wait(sid)
    out = [
        json.loads(x["content"]) for x in env.rt.repo.outputs(sid) if x["kind"] == "office_bench"
    ]
    assert out and out[0]["started"] is False and "已关闭" in out[0]["error"]
