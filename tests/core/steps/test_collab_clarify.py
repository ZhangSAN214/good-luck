"""协同（R6）：下游负责人动手前可以向上游作者提一次澄清问题（单桌，Fake）。"""

from __future__ import annotations

import re

from roundtable.core.allocation import IdentityScrubber
from roundtable.core.steps import restore_state
from roundtable.core.steps.collab import parse_clarify, strip_clarify

from .conftest import Table, assign_reply, user_text
from .test_collab_pipeline import THREE, run, script

OFFER = "补充规则（澄清提问）"
ANSWERING = "向你提了一个澄清问题"
REPLIED = "你的澄清问题的答复在"
QUESTION = "T1 的结论适用于哪种情况？"


def asking(t: Table, answer: str = "适用于一般情况，边界条件见第二段。"):
    """T2 的负责人提一次问题；上游作者按 answer 答复；其他调用按默认。"""
    base = t.fake._default

    def reply(model, messages):
        system = messages[0].content
        if ANSWERING in system:
            return answer
        if (
            OFFER in system
            and REPLIED not in system
            and '<your_subtask id="T2"' in user_text(messages)
        ):
            return f'<clarify to="T1">{QUESTION}</clarify>'
        return base(model, messages)

    t.fake._default = reply


def work_calls(t: Table, sid: str):
    return [
        x
        for x in t.fake.calls
        if "你负责下面 <your_subtask>" in x.messages[0].content
        and f'<your_subtask id="{sid}"' in user_text(x.messages)
    ]


def without_clarify(t: Table) -> None:
    rt = t.config.roundtable
    rule = rt.collab.clarify.model_copy(update={"enabled": False})
    collab = rt.collab.model_copy(update={"clarify": rule})
    t.ctx.config = t.config.model_copy(
        update={"roundtable": rt.model_copy(update={"collab": collab})}
    )


async def test_downstream_asks_upstream_once_and_works_with_the_answer():
    t = Table(9, pipeline=True)
    t.ctx.scrubber = IdentityScrubber.from_config(t.config.models)
    script(t, THREE, assign=assign_reply)
    asking(t)
    await run(t, "decompose", "volunteer", "assign")
    c = t.ctx.state.collab
    owners = {sid: o[0] for sid, o in c.assignment.owners.items()}
    (res,) = await run(t, "work")

    # 没有上游的子任务不提供提问；有上游的都提供（同一规则），没提问的只调用一次
    (t1,) = work_calls(t, "T1")
    assert OFFER not in t1.messages[0].content
    (t3,) = work_calls(t, "T3")
    assert OFFER in t3.messages[0].content and "T2 之一" in t3.messages[0].content

    # 上游作者收到问题并答复（只有一次）
    (ans,) = [x for x in t.fake.calls if ANSWERING in x.messages[0].content]
    assert ans.model == t.ctx.members[owners["T1"]]
    assert QUESTION in user_text(ans.messages) and '<downstream_subtask id="T2"' in user_text(
        ans.messages
    )
    assert f"代号「组员{owners['T1']}」" in ans.messages[0].content

    # 提问者在同一对话里收到答复后完成；成果里没有提问标签
    first, second = work_calls(t, "T2")
    assert second.messages[-2].role == "assistant" and QUESTION in second.messages[-2].content
    assert "<clarification>\n适用于一般情况" in second.messages[-1].content
    assert "<clarify" not in c.works[("T2", owners["T2"])]
    assert res.calls == 3 + 1 + 1  # 三块各一次 + 答复 + 提问后再做一次

    record = c.clarifications[("T2", owners["T2"])]
    assert record["to_subtask"] == "T1" and record["to_code"] == owners["T1"]
    assert record["question"] == QUESTION and record["answer"].startswith("适用于一般情况")
    (event,) = [e for e in t.events if e.type == "clarify"]
    assert event.code == owners["T2"]
    assert set(event.data) == {"subtask", "to_subtask", "to_code", "answered"}
    # 答复调用单独记为 clarify 步骤
    steps = [
        r[0] for r in t.repo._exec("SELECT step FROM calls WHERE session_id = ?", (t.session,))
    ]
    assert steps.count("clarify") == 1

    # 发给模型的内容没有模型 id
    for call in (ans, second):
        text = "\n".join(m.content for m in call.messages)
        for m in t.config.models.models:
            if m.id != call.model:
                assert not re.search(rf"(?<![0-9A-Za-z]){re.escape(m.id)}(?![0-9A-Za-z])", text)

    # 恢复后记录还在，重跑不会再问
    restored = restore_state(t.repo, t.session, 0).collab
    assert restored.clarifications == c.clarifications
    before = len(t.fake.calls)
    await run(t, "work")
    assert len(t.fake.calls) == before


async def test_resumed_after_the_answer_does_not_ask_again():
    t = Table(9, pipeline=True)
    script(t, THREE, assign=assign_reply)
    asking(t)
    await run(t, "decompose", "volunteer", "assign", "work")
    c = t.ctx.state.collab
    o2 = c.assignment.owners["T2"][0]
    del c.works[("T2", o2)]  # 模拟：答复已存库，提问者的成果还没存就中断了
    before = len(t.fake.calls)
    await run(t, "work")
    new = t.fake.calls[before:]
    assert not any(ANSWERING in x.messages[0].content for x in new)
    (redo,) = [x for x in new if '<your_subtask id="T2"' in user_text(x.messages)]
    assert "<clarification>" in redo.messages[-1].content
    assert ("T2", o2) in c.works


async def test_no_answer_still_lets_the_asker_finish():
    t = Table(9, pipeline=True)
    script(t, THREE, assign=assign_reply)
    asking(t, answer="")
    await run(t, "decompose", "volunteer", "assign", "work")
    c = t.ctx.state.collab
    o2 = c.assignment.owners["T2"][0]
    assert c.clarifications[("T2", o2)]["answer"] == ""
    second = work_calls(t, "T2")[-1]
    assert "（上游作者没有答复）" in second.messages[-1].content
    assert ("T2", o2) in c.works and o2 in t.ctx.active


async def test_disabled_clarify_adds_nothing():
    t = Table(9, pipeline=True)
    script(t, THREE, assign=assign_reply)
    asking(t)
    without_clarify(t)
    await run(t, "decompose", "volunteer", "assign", "work")
    assert not any(OFFER in x.messages[0].content for x in t.fake.calls)
    assert not t.ctx.state.collab.clarifications


def test_parse_clarify_only_accepts_a_lone_tag():
    allowed = ["T1", "T2"]
    assert parse_clarify('<clarify to="T2"> 问题 一 </clarify>', allowed, 300) == ("T2", "问题 一")
    assert parse_clarify("<clarify to=T9>问题</clarify>", allowed, 300) == ("T1", "问题")
    assert parse_clarify('<clarify to="T1">很长' + "字" * 50 + "</clarify>", allowed, 10) == (
        "T1",
        "很长" + "字" * 8,
    )
    assert parse_clarify('正文……<clarify to="T1">问题</clarify>', allowed, 300) is None
    assert parse_clarify('<clarify to="T1">  </clarify>', allowed, 300) is None
    assert parse_clarify('<clarify to="T1">问题</clarify>', [], 300) is None
    assert strip_clarify('成果正文<clarify to="T1">问题</clarify>') == "成果正文"
    assert strip_clarify('<clarify to="T1">问题</clarify>') == '<clarify to="T1">问题</clarify>'
