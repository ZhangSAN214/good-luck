"""翻日记：只在生活模式，每天有次数上限；成员在私聊里翻，翻到的内容放在 <diary> 里交回再回复。"""

from __future__ import annotations

import pytest

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.member import WorkModeError
from roundtable.core.member.memory import DiaryTool

from ...orchestrator.conftest import Env


def diaries(env: Env) -> None:
    env.rt.repo.add_memory(
        "V1", "day", "今天和用户去了一兰拉面店，排了好久的队。", period="2026-10-21"
    )
    env.rt.repo.add_memory("V1", "day", "下雨，一直待在宿舍看小说。", period="2026-10-22")
    env.rt.repo.add_memory_item("V1", "用户说下次想吃寿司", day="2026-10-21")


def test_find_by_date_and_keyword():
    env = Env()
    diaries(env)
    tool = DiaryTool(env.rt)
    assert tool.find("V1", "2026-10-22") == ["【2026-10-22 的日记】下雨，一直待在宿舍看小说。"]
    found = tool.find("V1", "拉面")
    assert found[0].startswith("【2026-10-21 的日记】今天和用户去了一兰拉面店")
    assert any("寿司" in x for x in tool.find("V1", "想吃寿司"))
    assert tool.find("V2", "拉面") == []  # 只翻自己的


def test_daily_limit_and_life_mode_only():
    env = Env()
    diaries(env)
    tool = DiaryTool(env.rt)
    limit = env.config.roundtable.chat.memory.diary.daily_reads
    assert tool.remaining("V1") == limit
    for _ in range(limit):
        assert tool.read("V1", "拉面") is not None
    assert tool.read("V1", "拉面") is None and tool.remaining("V1") == 0
    assert tool.remaining("V2") == limit  # 每位成员各自算，规则相同
    with pytest.raises(WorkModeError):
        tool.remaining("V1", mode="work")
    with pytest.raises(WorkModeError):
        tool.read("V1", "拉面", mode="work")


async def test_member_reads_diary_in_dm_then_replies():
    env = Env()
    diaries(env)
    replies = iter(
        ['<tool_call name="read_diary">拉面</tool_call>', "想起来了，是一兰，排了好久。"]
    )
    fallback = env.fake._default

    def reply(model, messages):
        if "你在一个私聊里" in messages[0].content:
            return next(replies)
        return fallback(model, messages)

    env.fake._default = reply
    dm = ChatHub(env.rt).room(dm_room("V1"))
    msg, speakers = dm.post("上次那家拉面店叫什么")
    (said,) = await dm.replies(msg["id"], speakers)
    assert said["text"] == "想起来了，是一兰，排了好久。"
    first, second = env.fake.calls[-2:]
    assert '<tool_call name="read_diary">' in first.messages[0].content
    assert "今天还能翻 3 次日记" in first.messages[-1].content
    assert "<diary>\n【2026-10-21 的日记】今天和用户去了一兰拉面店" in second.messages[-1].content
    assert second.messages[-2].role == "assistant"
    assert DiaryTool(env.rt).remaining("V1") == 2


async def test_work_group_has_no_diary_tool_and_reads_used_up_hide_it():
    env = Env()
    work = ChatHub(env.rt).room("work")
    msg, speakers = work.post("@阿一 在吗")
    await work.replies(msg["id"], speakers)
    assert "read_diary" not in env.fake.calls[-1].messages[0].content
    for _ in range(3):
        DiaryTool(env.rt).read("V1", "x")
    dm = ChatHub(env.rt).room(dm_room("V1"))
    msg, speakers = dm.post("在吗")
    await dm.replies(msg["id"], speakers)
    assert "read_diary" not in env.fake.calls[-1].messages[0].content
