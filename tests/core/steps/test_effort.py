"""防偷懒：实质内容规则、打回重做一次、仍不合格标记敷衍（产出保留，统筹看到标记）。"""

from __future__ import annotations

import json

import pytest

from roundtable.core.config import load_config
from roundtable.core.steps import get_step, restore_state
from roundtable.core.steps.effort import min_chars, similarity, text_problems

from .conftest import MEMBERS, QUESTION, REASONING, REVISED, Table, good_review

RULE = load_config().roundtable.effort_check
GOOD = f"答案：最大值 2，最小值 -2。{REASONING}"


async def run(table: Table, *steps: str):
    return [await get_step(s).run(table.ctx) for s in steps]


def problems(text, expected=1200, peers=None):
    return text_problems(text, rule=RULE, question=QUESTION, expected_tokens=expected, peers=peers)


# --- 规则 -----------------------------------------------------------------------


def test_substantive_answer_passes():
    assert problems(GOOD) == []


@pytest.mark.parametrize("text", ["略", "同上。", "  TODO ", "...", "同意！"])
def test_empty_phrases(text):
    assert problems(text) == ["只有空话，没有实质内容"]


def test_refusal():
    found = problems("抱歉，我无法回答这个问题。")
    assert "拒绝作答或只表示无法完成" in found


def test_long_answer_mentioning_apology_is_not_refusal():
    text = "抱歉，题目中区间写法有歧义，下面按闭区间处理。" + REASONING
    assert "拒绝作答或只表示无法完成" not in problems(text)


def test_too_short_relative_to_expected_length():
    assert min_chars(RULE, 1200) == 120 and min_chars(RULE, 50) == RULE.min_chars
    assert any("内容过短" in p for p in problems("最大值 2，最小值 -2，因为求导后比较。", 1200))
    # 预估很短的题目，简短但完整的回答合格
    assert problems("1+1=2：一加一等于二，这是加法定义。", expected=100) == []


def test_restating_the_question():
    assert "基本只是复述题目，没有作答" in problems(QUESTION + " 请写出完整过程。", expected=10)


def test_copying_a_peer():
    peers = {"乙": GOOD + "补充。", "丙": "完全不同的另一种解法。" * 20}
    found = problems(GOOD, peers=peers)
    assert found == ["与乙的答案几乎相同，疑似照抄"]
    # 太短的文本不判照抄（简短结论相同很正常）
    short = "最大值为 2，最小值为 -2，见端点与驻点比较。"
    assert problems(short, expected=1, peers={"乙": short}) == []


def test_similarity_ignores_whitespace_and_punctuation():
    assert similarity("a, b c", "abc") == 1.0
    assert similarity("", "abc") == 0.0


# --- 作答：重做一次 ----------------------------------------------------------------


async def test_lazy_answer_is_redone_once_and_replaced(table):
    table.fake.queue("b1", "略")
    await run(table, "answer")
    calls = table.calls_for("b1")
    assert len(calls) == 2  # 原调用 + 重做
    redo = calls[1].messages
    assert [m.role for m in redo] == ["system", "user", "assistant", "user"]
    assert redo[2].content == "略"  # 上一次的输出
    assert "只有空话" in redo[3].content and "打回重做" in redo[0].content
    assert redo[1].content == calls[0].messages[1].content  # 原题目原样保留
    assert table.ctx.state.answers["甲"].startswith("b1 的答案")
    record = table.ctx.state.effort[("answer", "甲", "")]
    assert record.status == "redone" and not record.lazy
    assert [e.type for e in table.events].count("effort_redo") == 1
    # 合格的组员不重做
    assert len(table.calls_for("b2")) == 1 and len(table.calls_for("b3")) == 1


async def test_still_lazy_after_redo_is_flagged_and_kept(table):
    table.fake.queue("b1", "略", "抱歉，我无法回答。")
    [result] = await run(table, "answer")
    assert table.ctx.state.answers["甲"] == "抱歉，我无法回答。"  # 产出保留
    record = table.ctx.state.effort[("answer", "甲", "")]
    assert record.lazy and record.reasons == ("只有空话，没有实质内容",)
    assert "拒绝作答或只表示无法完成" in record.final_reasons
    assert table.ctx.state.flagged("甲") and not table.ctx.state.flagged("乙")
    assert any("敷衍" in n for n in result.notes)
    assert "effort_flagged" in [e.type for e in table.events]
    stored = table.repo.outputs(table.session, kind="effort")
    assert json.loads(stored[0]["content"])["status"] == "lazy"


async def test_redo_prompt_is_identical_for_everyone():
    t = Table(3)
    for model in MEMBERS.values():
        t.fake.queue(model, "略")
    await run(t, "answer")
    redo_texts = {t.calls_for(m)[1].messages[-1].content for m in MEMBERS.values()}
    redo_systems = {
        t.calls_for(m)[1].messages[0].content.replace(f"组员{c}", "") for c, m in MEMBERS.items()
    }
    assert len(redo_texts) == 1 and len(redo_systems) == 1


async def test_redo_disabled_flags_without_calling(table):
    rule = table.config.roundtable.effort_check.model_copy(update={"redo": False})
    rt = table.config.roundtable.model_copy(update={"effort_check": rule})
    table.ctx.config = table.config.model_copy(update={"roundtable": rt})
    table.fake.queue("b1", "略")
    await run(table, "answer")
    assert len(table.calls_for("b1")) == 1
    record = table.ctx.state.effort[("answer", "甲", "")]
    assert record.lazy and not record.redone


async def test_effort_check_disabled(table):
    rule = table.config.roundtable.effort_check.model_copy(update={"enabled": False})
    rt = table.config.roundtable.model_copy(update={"effort_check": rule})
    table.ctx.config = table.config.model_copy(update={"roundtable": rt})
    table.fake.queue("b1", "略")
    await run(table, "answer")
    assert len(table.calls_for("b1")) == 1 and table.ctx.state.effort == {}


async def test_flagged_answer_is_marked_for_the_coordinator(table):
    table.fake.queue("b1", "略", "略")
    await run(table, "answer", "review", "revise", "synthesize")
    prompt = table.prompts_for("f1")[-1]
    assert 'code="组员甲" flagged=' in prompt
    assert 'code="组员乙">' in prompt  # 合格的没有标记


# --- 互评与修订 -------------------------------------------------------------------


def all_vague_reviews(model, messages):
    from .conftest import labels_in

    return json.dumps(
        {
            "reviews": [
                {"target": f"组员{c}", "verdict": "correct", "checked": "很好"}
                for c in labels_in(messages)
            ]
        },
        ensure_ascii=False,
    )


async def test_review_with_no_valid_review_is_redone(table):
    await run(table, "answer")

    state = {"n": 0}

    def first_vague(model, messages):
        if model == "b1" and "审阅每一份答案" in messages[0].content:
            state["n"] += 1
            if state["n"] == 1:
                return all_vague_reviews(model, messages)
        from .conftest import default_reply

        return default_reply(model, messages)

    table.fake._default = first_vague
    await run(table, "review")
    assert len([c for c in table.calls_for("b1") if "审阅每一份答案" in c.messages[0].content]) == 2
    assert all(r.valid for r in table.ctx.state.reviews["甲"])
    assert table.ctx.state.effort[("review", "甲", "")].status == "redone"


async def test_review_still_vague_is_flagged(table):
    await run(table, "answer")

    def vague(model, messages):
        if model == "b1" and "审阅每一份答案" in messages[0].content:
            return all_vague_reviews(model, messages)
        return good_review(model, messages)

    table.fake._default = vague
    await run(table, "review")
    record = table.ctx.state.effort[("review", "甲", "")]
    assert record.lazy and "所有评审都无效" in record.final_reasons[0]
    assert not any(r.valid for r in table.ctx.state.reviews["甲"])


async def test_revision_without_responses_is_redone(table):
    await run(table, "answer", "review")
    table.fake.queue("b1", f"## 修订后的答案\nb1 的修订稿。{REVISED}\n\n## 对审阅意见的回应\n")
    await run(table, "revise")
    record = table.ctx.state.effort[("revise", "甲", "")]
    assert record.reasons == ("没有回应审阅意见",) and record.status == "redone"
    assert table.ctx.state.revisions["甲"].decisions  # 重做后的回应已解析


async def test_revision_copying_a_peer_is_flagged(table):
    await run(table, "answer", "review")
    peer = table.ctx.state.answers["乙"]
    copied = f"## 修订后的答案\n{peer}\n\n## 对审阅意见的回应\n- 组员乙 · 问题 1：采纳 —— 对"
    table.fake.queue("b1", copied, copied)
    await run(table, "revise")
    record = table.ctx.state.effort[("revise", "甲", "")]
    assert record.lazy and "疑似照抄" in record.final_reasons[0]


async def test_effort_survives_restore(table):
    table.fake.queue("b1", "略", "略")
    await run(table, "answer")
    restored = restore_state(table.repo, table.session, 0)
    assert restored.effort == table.ctx.state.effort and restored.flagged("甲")
