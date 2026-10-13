"""R5：协同 / 讨论中途插话——下一步开始时带给所有人，只应用一次，之后的步骤继续带着。"""

from __future__ import annotations

import pytest

from roundtable.core.notes import (
    NOTE_MAX_CHARS,
    NOTE_MAX_PENDING,
    NoteError,
    check_can_add,
    clean_note,
)
from roundtable.core.routing import Question

from ..steps.conftest import user_text
from .conftest import MEDIUM, Env

NOTE = "请重点检查端点值，别漏掉 x=±2"


def system_and_user(call) -> str:
    return "\n".join(m.content for m in call.messages)


async def test_note_before_start_reaches_every_call():
    env = Env()
    sid = env.orc.open(Question(MEDIUM), seed=1)
    env.rt.repo.add_note(sid, NOTE)
    r = await env.orc.run(sid)
    assert r.status == "completed"
    answer_calls = [c for c in env.fake.calls if "独立完成同一道题" in c.messages[0].content]
    assert answer_calls and all(NOTE in system_and_user(c) for c in answer_calls)
    # 同一步骤所有成员看到的补充完全相同
    assert len({system_and_user(c).count(NOTE) for c in answer_calls}) == 1
    applied = [e for _, e in env.events if e.type == "notes_applied"]
    assert len(applied) == 1 and applied[0].data["count"] == 1
    notes = env.rt.repo.notes(sid)
    assert notes[0]["applied_step"] == "answer"


async def test_note_added_mid_run_applies_at_next_step_and_stays():
    env = Env()
    sid = env.orc.open(Question(MEDIUM), seed=1)
    inner = env.fake._default
    added = []

    def reply(model, messages):
        if not added and "独立完成同一道题" in messages[0].content:
            added.append(env.rt.repo.add_note(sid, NOTE))
        return inner(model, messages) if callable(inner) else inner

    env.fake._default = reply
    await env.orc.run(sid)
    answer = [c for c in env.fake.calls if "独立完成同一道题" in c.messages[0].content]
    assert all(NOTE not in system_and_user(c) for c in answer)  # 进行中的调用不被打断
    later = [c for c in env.fake.calls if "汇总成一份结论" in c.messages[0].content]
    assert later and all(NOTE in system_and_user(c) for c in later)  # 之后的步骤都带着
    note = env.rt.repo.notes(sid)[0]
    assert note["applied_step"] in {"review", "revise", "synthesize"}
    assert len([e for _, e in env.events if e.type == "notes_applied"]) == 1


async def test_anonymous_notes_do_not_leak_identity():
    env = Env()
    sid = env.orc.open(Question(MEDIUM), seed=1, anonymous=True)
    env.rt.repo.add_note(sid, NOTE)
    await env.orc.run(sid)
    ids = {m.id for m in env.config.models.models} | {m.vendor for m in env.config.models.models}
    for c in env.fake.calls:
        if NOTE in system_and_user(c):
            text = user_text(c.messages).lower()
            assert not [i for i in ids if len(i) > 3 and i.lower() in text]


def test_note_validation():
    assert clean_note("  你好 ") == "你好"
    with pytest.raises(NoteError):
        clean_note("   ")
    with pytest.raises(NoteError):
        clean_note("x" * (NOTE_MAX_CHARS + 1))
    check_can_add("running", 0)
    with pytest.raises(NoteError):
        check_can_add("completed", 0)
    with pytest.raises(NoteError):
        check_can_add("running", NOTE_MAX_PENDING)


def test_repository_note_lifecycle():
    env = Env()
    sid = env.orc.open(Question(MEDIUM), seed=1)
    repo = env.rt.repo
    a, b = repo.add_note(sid, "甲"), repo.add_note(sid, "乙")
    assert [n["text"] for n in repo.pending_notes(sid)] == ["甲", "乙"]
    repo.mark_notes_applied(sid, [a], 0, "answer")
    assert repo.applied_notes(sid) == ["甲"]
    assert [n["id"] for n in repo.pending_notes(sid)] == [b]
    assert len(repo.notes(sid)) == 2
