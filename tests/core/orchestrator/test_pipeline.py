"""协同流水线整桌（阶段 21）：拆分不合格 → 模板 → 分工 → 逐段交接 → 执行生成 → 审查 → 合并。"""

from __future__ import annotations

import json
import re

from roundtable.core.routing import Question
from roundtable.core.service import RoundtableService

from .conftest import Env
from .test_media import choice, make_env, script

QUESTION = "做一张四格人物介绍卡，四个角色各一格"
WHOLE = json.dumps(
    {
        "subtasks": [
            {
                "id": "T1",
                "kind": "write",
                "title": "完成整道题",
                "requirements": "独立完成题目的全部要求",
                "acceptance": "覆盖全部要求",
            }
        ]
    },
    ensure_ascii=False,
)


def whole_task(env: Env) -> None:
    script(
        env,
        **{
            "把任务拆成子任务": lambda m, msgs: WHOLE,
            "没有通过代码检查": lambda m, msgs: WHOLE,
        },
    )


def pipeline_env():
    env = make_env(pipeline=True)
    whole_task(env)
    return env


async def run(env=None, anonymous=False):
    env = env or pipeline_env()
    r = await env.orc.start(
        Question(QUESTION), choice(workflow="collab"), seed=3, anonymous=anonymous
    )
    return env, r


def output(env, sid, kind):
    return [json.loads(o["content"]) for o in env.rt.repo.outputs(sid, kind=kind)]


async def test_full_pipeline_via_template_runs_end_to_end():
    env, r = await run()
    assert r.status == "completed"
    subtasks = output(env, r.session_id, "subtasks")[0]
    assert subtasks["pipeline"] and subtasks["info"]["source"] == "template"
    assert subtasks["info"]["template"] == "creation"  # 有画图模型、没有图片附件、题目是创作
    kinds = [s["kind"] for s in subtasks["subtasks"]]
    assert kinds == ["write", "prompt", "generate", "review", "assemble"]
    assert [s["media"] for s in subtasks["subtasks"]][2] == "image"
    # 成员：每人至少一块，每块恰好一人
    assignment = output(env, r.session_id, "assignment")[-1]
    owners = {a["subtask"]: a["members"] for a in assignment["assignments"]}
    assert all(len(v) == 1 for v in owners.values())
    assert len({v[0] for v in owners.values()}) == 3
    # 写提示词的人不审查自己提示词生成的结果；成员够多时执行生成者也不审查自己的
    assert owners["T4"] != owners["T2"] and owners["T4"] != owners["T3"]
    # 执行生成：负责人先确认提示词（短调用），再由代码调用画图模型，登记在负责人名下
    marker = "生成之前，请先确认这份提示词可以直接用"
    confirm = [c for c in env.fake.calls if marker in c.messages[0].content]
    assert len(confirm) == 1
    jobs = env.rt.repo.media_jobs(r.session_id)
    gen = [j for j in jobs if j["subtask"] == "T3" and j["step"] == "work"]
    assert len(gen) == 1 and gen[0]["code"] == owners["T3"][0] and gen[0]["state"] == "completed"
    assert not [j for j in jobs if j["subtask"] not in ("T3",)]  # 其他类型不生成媒体
    # 交接链：下游开始时记录谁把什么交给谁
    handoffs = output(env, r.session_id, "handoff")
    pairs = {(h["from_subtask"], h["to_subtask"]) for h in handoffs}
    assert {("T1", "T2"), ("T2", "T3"), ("T3", "T4"), ("T3", "T5"), ("T4", "T5")} <= pairs
    assert {h["gives"] for h in handoffs if h["to_subtask"] == "T3"} == {"画图提示词"}
    assert any(e.type == "handoff" for _, e in env.events)
    # 审查类子任务只有一个
    reviews = [c for c in env.fake.calls if "（类型：审查）" in c.messages[0].content]
    assert len(reviews) == 1
    assert output(env, r.session_id, "merge")


async def test_pipeline_view_and_events_have_no_identity_when_anonymous():
    env, r = await run(anonymous=True)
    assert r.status == "completed"
    svc = RoundtableService(env.rt)
    data = svc.session(r.session_id)
    text = json.dumps(data, ensure_ascii=False, default=str) + json.dumps(
        [(e.type, e.code, e.data) for _, e in env.events], ensure_ascii=False, default=str
    )
    terms = {m.id for m in env.config.models.models} | set(env.config.personas.nicknames.values())
    terms |= {m.vendor for m in env.config.models.models}
    found = [t for t in terms if re.search(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", text)]
    assert found == []
    assert set(data["tables"][0]["codes"]) <= set(env.config.personas.codes)
    await svc.aclose()


async def test_pipeline_resume_does_not_repeat_calls():
    env, r = await run()
    before = len(env.fake.calls)
    again = await env.orc.resume(r.session_id)
    assert again.status == "completed" and len(env.fake.calls) == before
    handoffs = output(env, r.session_id, "handoff")
    keys = {(h["from_subtask"], h["from_code"], h["to_subtask"], h["to_code"]) for h in handoffs}
    assert len(keys) == len(handoffs)


async def test_without_media_models_the_template_falls_to_documents():
    env = Env(confirm_threshold_usd=100.0, pipeline=True)  # 没有画图模型
    whole_task(env)
    r = await env.orc.start(Question(QUESTION), choice(workflow="collab"), seed=3)
    assert r.status == "completed"
    info = output(env, r.session_id, "subtasks")[0]["info"]
    assert info["template"] == "document"
    assert not env.rt.repo.media_jobs(r.session_id)
