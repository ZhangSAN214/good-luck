"""聊天里的活交给办公室：记号取出来、选项按钮、题目只带要做的事和原话、卡片状态。"""

from __future__ import annotations

from roundtable.core import chat_handoff as h

from .orchestrator.conftest import Env


def test_handoff_marker_is_taken_out_of_the_words():
    text, task = h.extract("行，我去弄一下。[交办:按 9 月行情模拟 10 月 A 股操作，给出每日记录表]")
    assert text == "行，我去弄一下。"
    assert task == "按 9 月行情模拟 10 月 A 股操作，给出每日记录表"
    assert h.extract("没有记号") == ("没有记号", None)
    assert h.extract("[交办：画一张猫]")[1] == "画一张猫"


def test_choice_marker_becomes_two_to_four_short_buttons():
    text, opts = h.choices("你想先看哪个？[选项:银行|新能源|消费|医药|科技]")
    assert text == "你想先看哪个？" and opts == ["银行", "新能源", "消费", "医药"]
    assert h.choices("[选项:只有一个]")[1] == []


def test_question_keeps_task_first_and_wraps_the_request():
    q = h.question("做一张表", "帮我把这个月花销列个表吧")
    assert q.startswith("做一张表") and "<request>" in q
    assert h.question("做一张表", "做一张表") == "做一张表"


def test_mark_and_origin_round_trip():
    env = Env()
    rt = env.rt
    msg = rt.repo.add_chat_message("main", "V1", "我去弄", meta={"handoff": h.pending("做表")})
    h.remember(rt, "S1", room="main", member="V1", message_id=msg["id"])
    assert h.origin(rt, "S1")["member"] == "V1"
    h.mark(rt, msg["id"], state=h.RUNNING, session_id="S1")
    assert rt.repo.chat_message(msg["id"])["meta"]["handoff"]["state"] == "running"
    h.forget(rt, "S1")
    assert h.origin(rt, "S1") is None
    card = h.result_card(sid="S1", title="做表", ok=True, text="x" * 7000, files=[], cost=0.01)
    assert card["cut"] and len(card["text"]) <= h.RESULT_CHARS + 1
