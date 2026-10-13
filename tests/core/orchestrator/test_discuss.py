"""R6：分工讨论——成员对分工提建议，统筹最多调整一次；调整不合规则就保持原分工。"""

from __future__ import annotations

import json
import re

from roundtable.core.steps.collab import AssignStep

from .test_media import script
from .test_pipeline import output, pipeline_env, run

DISCUSS = "对分工方案发表一次简短的意见"
ADJUST = "你只有这一次调整机会"


def suggest(m, msgs):
    return json.dumps(
        {
            "agree": False,
            "suggestions": [{"subtask": "T1", "suggestion": "换人负责", "reason": "更熟悉这一块"}],
        },
        ensure_ascii=False,
    )


def agree(m, msgs):
    return json.dumps({"agree": True, "suggestions": []})


def plan_rows(msgs) -> list[tuple[str, str]]:
    text = "\n".join(x.content for x in msgs if x.role == "user")
    block = text.split("<plan>")[1].split("</plan>")[0]
    return re.findall(r"^(T\d+)「[^」]*」：(.+)$", block, re.M)


def swapped(m, msgs):
    rows = dict(plan_rows(msgs))
    ids = list(rows)
    rows[ids[0]], rows[ids[1]] = rows[ids[1]], rows[ids[0]]
    return json.dumps(
        {
            "assignments": [{"subtask": k, "members": [v]} for k, v in rows.items()],
            "rationale": "采纳建议，交换了两块",
        },
        ensure_ascii=False,
    )


def kept(m, msgs):
    rows = plan_rows(msgs)
    return json.dumps(
        {"assignments": [{"subtask": k, "members": [v]} for k, v in rows], "rationale": "无需调整"},
        ensure_ascii=False,
    )


def calls(env, marker):
    return [c for c in env.fake.calls if marker in c.messages[0].content]


async def test_everyone_agrees_keeps_plan_without_coordinator_call():
    env = pipeline_env()
    script(env, **{DISCUSS: agree})
    env, r = await run(env)
    assert r.status == "completed"
    assert len(calls(env, DISCUSS)) == 3 and calls(env, ADJUST) == []
    result = output(env, r.session_id, "discuss_result")[0]
    assert result["adjusted"] is False
    assert len(output(env, r.session_id, "assignment")) == 1


async def test_suggestions_lead_to_one_adjustment(monkeypatch):
    monkeypatch.setattr(AssignStep, "_pipeline_problems", staticmethod(lambda *a: []))
    env = pipeline_env()
    script(env, **{DISCUSS: suggest, ADJUST: swapped})
    env, r = await run(env)
    assert r.status == "completed"
    assert len(calls(env, ADJUST)) == 1  # 统筹最多调整一次
    first, last = output(env, r.session_id, "assignment")
    assert first != last
    owners = {a["subtask"]: a["members"] for a in last["assignments"]}
    before = {a["subtask"]: a["members"] for a in first["assignments"]}
    assert owners["T1"] == before["T2"] and owners["T2"] == before["T1"]
    assert output(env, r.session_id, "discuss_result")[0]["adjusted"] is True
    # 所有成员收到的讨论提示词完全相同（除代号）
    texts = {re.sub(r"组员.|[^\s]+·\S+", "X", c.messages[1].content) for c in calls(env, DISCUSS)}
    assert len(texts) == 1


async def test_invalid_adjustment_keeps_original_plan():
    env = pipeline_env()
    bad = lambda m, msgs: json.dumps({"assignments": [], "rationale": "乱写"})  # noqa: E731
    script(env, **{DISCUSS: suggest, ADJUST: bad})
    env, r = await run(env)
    assert r.status == "completed"
    result = output(env, r.session_id, "discuss_result")[0]
    assert result["adjusted"] is False and "保持原分工" in result["why"]
    assert len(output(env, r.session_id, "assignment")) == 1


async def test_unchanged_plan_is_not_an_adjustment():
    env = pipeline_env()
    script(env, **{DISCUSS: suggest, ADJUST: kept})
    env, r = await run(env)
    assert output(env, r.session_id, "discuss_result")[0]["adjusted"] is False


async def test_garbled_discussion_counts_as_no_opinion():
    env = pipeline_env()
    script(env, **{DISCUSS: lambda m, msgs: "我没意见"})
    env, r = await run(env)
    assert r.status == "completed"
    assert all(d["degraded"] for d in output(env, r.session_id, "discuss"))
    assert calls(env, ADJUST) == []


async def test_anonymous_discussion_has_no_identity_leak():
    env = pipeline_env()
    script(env, **{DISCUSS: suggest, ADJUST: kept})
    env, r = await run(env, anonymous=True)
    ids = {m.id for m in env.config.models.models} | {m.vendor for m in env.config.models.models}
    nick = set(env.config.personas.nicknames.values())
    for c in calls(env, DISCUSS) + calls(env, ADJUST):
        text = "\n".join(x.content for x in c.messages).lower()
        assert not [t for t in ids | nick if len(t) > 2 and t.lower() in text]


async def test_discussion_skipped_when_disabled():
    env = pipeline_env()
    cfg = env.rt.config.roundtable
    off = cfg.collab.discussion.model_copy(update={"enabled": False})
    env.rt.config = env.rt.config.model_copy(
        update={
            "roundtable": cfg.model_copy(
                update={"collab": cfg.collab.model_copy(update={"discussion": off})}
            )
        }
    )
    env, r = await run(env)
    assert calls(env, DISCUSS) == [] and output(env, r.session_id, "discuss_result") == []
