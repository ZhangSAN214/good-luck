"""R8：按子任务类型覆盖 work 步骤的参数——同一类型所有成员相同，与成员是谁无关。"""

from __future__ import annotations

from .test_pipeline import pipeline_env, run


async def test_work_params_follow_subtask_kind_for_every_member():
    env, r = await run(pipeline_env())
    assert r.status == "completed"
    rules = env.config.roundtable.collab.kinds
    cap = {"T1": None, "T2": rules["prompt"].step_params["max_tokens"]}
    cap["T4"] = rules["review"].step_params["max_tokens"]
    default = env.config.roundtable.step_params["work"]["max_tokens"]
    seen: dict[str, set[int]] = {}
    for call in env.fake.calls:
        user = call.messages[1].content if len(call.messages) > 1 else ""
        if "你负责下面 <your_subtask" not in call.messages[0].content:
            continue
        for sid in ("T1", "T2", "T4"):
            if f'your_subtask id="{sid}"' in user:
                seen.setdefault(sid, set()).add(call.params["max_tokens"])
    assert seen["T1"] == {default}  # 撰写没有覆盖，用 work 步骤默认值
    assert seen["T2"] == {cap["T2"]} and seen["T4"] == {cap["T4"]}  # 每个成员一样
