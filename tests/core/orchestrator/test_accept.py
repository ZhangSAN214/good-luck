"""R7：合并前逐条验收——不合格的子任务退回作者改一次，修改稿进入合并。"""

from __future__ import annotations

import json

from roundtable.core.steps.collab_schemas import parse_acceptance

from .test_media import script
from .test_pipeline import output, pipeline_env, run

ACCEPT = "逐条验收"
FIX = "这是最后一次修改机会"
MERGE = "合并成一份完整成果"
REVISED = "验收后的修改稿：补上了缺失的论据"


def verdict(*failed: str):
    def reply(m, msgs):
        text = "\n".join(x.content for x in msgs if x.role == "user")
        ids = [f"T{i}" for i in range(1, 7) if f'id="T{i}"' in text]
        return json.dumps(
            {
                "items": [
                    {
                        "subtask": sid,
                        "passed": sid not in failed,
                        "problems": [f"{sid} 的验收标准第 1 条没有满足"] if sid in failed else [],
                    }
                    for sid in ids
                ]
            },
            ensure_ascii=False,
        )

    return reply


def fixed_reply(m, msgs):
    return f"## 修订后的答案\n{REVISED}\n\n## 对审阅意见的回应\n- 采纳 —— 已补充"


def calls(env, marker):
    return [c for c in env.fake.calls if marker in c.messages[0].content]


async def test_failed_subtask_is_sent_back_once_and_fix_reaches_merge():
    env = pipeline_env()
    script(env, **{ACCEPT: verdict("T2"), FIX: fixed_reply})
    env, r = await run(env)
    assert r.status == "completed"
    acc = output(env, r.session_id, "acceptance")[0]
    assert acc["fixed"] == ["T2"]
    assert [x["passed"] for x in acc["items"] if x["subtask"] == "T2"] == [False]
    assert len(calls(env, FIX)) == 1  # 只退回一次，只找 T2 的作者
    merge_calls = calls(env, MERGE)
    assert merge_calls and REVISED in "\n".join(x.content for x in merge_calls[0].messages)
    fixes = [o for o in output(env, r.session_id, "rework") if o.get("accepted_fix")]
    assert len(fixes) == 1 and fixes[0]["subtask"] == "T2"


async def test_all_passed_makes_no_fix_calls():
    env = pipeline_env()
    script(env, **{ACCEPT: verdict()})
    env, r = await run(env)
    assert len(calls(env, ACCEPT)) == 1 and calls(env, FIX) == []
    assert output(env, r.session_id, "acceptance")[0]["fixed"] == []


async def test_garbled_verdict_is_treated_as_no_verdict():
    env = pipeline_env()
    script(env, **{ACCEPT: lambda m, msgs: "都不错"})
    env, r = await run(env)
    assert r.status == "completed" and calls(env, FIX) == []
    assert output(env, r.session_id, "acceptance")[0]["degraded"] is True


async def test_fix_limit_caps_the_number_of_returned_subtasks():
    env = pipeline_env()
    rt = env.rt.config.roundtable
    rule = rt.collab.acceptance.model_copy(update={"max_fix_items": 1})
    env.rt.config = env.rt.config.model_copy(
        update={
            "roundtable": rt.model_copy(
                update={"collab": rt.collab.model_copy(update={"acceptance": rule})}
            )
        }
    )
    script(env, **{ACCEPT: verdict("T1", "T2", "T4"), FIX: fixed_reply})
    env, r = await run(env)
    assert output(env, r.session_id, "acceptance")[0]["fixed"] == ["T1"]
    assert len(calls(env, FIX)) == 1


async def test_media_subtask_is_not_inspected():
    env = pipeline_env()
    seen = []

    def spy(m, msgs):
        seen.append("\n".join(x.content for x in msgs if x.role == "user"))
        return verdict()(m, msgs)

    script(env, **{ACCEPT: spy})
    await run(env)
    assert seen and 'id="T3"' not in seen[0]  # T3 是执行生成（画图）


async def test_acceptance_can_be_turned_off():
    env = pipeline_env()
    rt = env.rt.config.roundtable
    off = rt.collab.acceptance.model_copy(update={"enabled": False})
    env.rt.config = env.rt.config.model_copy(
        update={
            "roundtable": rt.model_copy(
                update={"collab": rt.collab.model_copy(update={"acceptance": off})}
            )
        }
    )
    env, r = await run(env)
    assert calls(env, ACCEPT) == [] and output(env, r.session_id, "acceptance") == []


def test_parse_acceptance_rules():
    ids = ["T1", "T2", "T3"]
    text = json.dumps(
        {
            "items": [
                {"subtask": "t1", "passed": False, "problems": ["缺结论"]},
                {"subtask": "T2", "passed": False, "problems": []},  # 没写问题：作者无从修改
                {"subtask": "T9", "passed": False, "problems": ["未知"]},
            ]
        },
        ensure_ascii=False,
    )
    acc = parse_acceptance(text, ids)
    assert acc.failed() == ["T1"]
    assert acc.results["T2"] == (True, ()) and acc.results["T3"] == (True, ())
    assert "T9" not in acc.results
