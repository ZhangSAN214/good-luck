from __future__ import annotations

import json

from roundtable.core.dsh.stream import DshWatch
from roundtable.core.supercomputer.runner import RunResult


def ev(**kw) -> str:
    return json.dumps(kw, ensure_ascii=False)


def test_counts_steps_and_stops_once():
    w = DshWatch(2)
    assert w.feed(ev(type="session", sessionId="s")) is None
    assert w.feed(ev(type="tool_call")) is None
    assert w.feed(ev(type="tool_call")) is None
    assert "步数上限" in (w.feed(ev(type="tool_call")) or "")
    assert w.feed(ev(type="tool_call")) is None  # 只喊一次
    assert w.feed("不是 JSON") is None and w.feed("[1]") is None


def test_unlimited_and_final_answer():
    w = DshWatch(0)
    for _ in range(500):
        assert w.feed(ev(type="tool_call")) is None
    w.feed(ev(type="text", text="中间的话"))
    w.feed(ev(type="final", text="最后的回答"))
    raw = RunResult(0, "事件流原文")
    assert w.finish(raw).output == "最后的回答" and w.finish(raw).ok
    assert w.followup() is None


def test_answer_falls_back_to_texts():
    w = DshWatch(5)
    w.feed(ev(type="text", text="第一段"))
    w.feed(ev(type="text", text="第二段"))
    assert w.finish(RunResult(1, "x")).output == "第一段\n\n第二段"


def test_stopped_with_wrapup():
    inner = DshWatch(1)
    w = DshWatch(1, lambda sid: (["dsh", "--session-id", sid], "收尾", inner))
    w.feed(ev(type="session", sessionId="abc"))
    w.feed(ev(type="text", text="写好了 api.py"))
    w.feed(ev(type="tool_call"))
    w.feed(ev(type="tool_call"))
    argv, text, got = w.followup()
    assert argv[-1] == "abc" and text == "收尾" and got is inner
    out = w.finish(RunResult(0, ""), RunResult(0, "还差测试"))
    assert out.ok and "步数上限 1 步" in out.output
    assert "写好了 api.py" in out.output and "还差测试" in out.output
    # 收尾也失败：算失败，让别人接手
    assert not w.finish(RunResult(0, ""), RunResult(1, "")).ok


def test_stopped_without_session_cannot_wrap_up():
    w = DshWatch(1, lambda sid: (["x"], "y", DshWatch(1)))
    w.feed(ev(type="tool_call"))
    w.feed(ev(type="tool_call"))
    assert w.followup() is None
    assert not w.finish(RunResult(0, "")).ok
