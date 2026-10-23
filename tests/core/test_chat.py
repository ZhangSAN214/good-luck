"""群聊：谁来回应、@ 人、调用记账、预算、掉线、一键清空。"""

from __future__ import annotations

import random
from types import SimpleNamespace

from roundtable.core.chat import GroupChat, chat_members, clean_reply, mentioned
from roundtable.core.providers import ErrorKind

from .orchestrator.conftest import Env


def chat(env: Env, seed: int = 0) -> GroupChat:
    return GroupChat(env.rt, rng=random.Random(seed))


def test_one_member_per_vendor_using_the_chat_tier():
    env = Env()
    members = chat_members(env.config, {m.id for m in env.config.models.models})
    picked = {m.id: m.model_id for m in members}
    # 有节电档的用节电档；只有全力档的用全力档
    assert picked == {"V1": "b1", "V2": "b2", "V3": "b3", "V4": "f3", "V5": "f4", "V6": "f5"}
    assert all(m.available for m in members)
    # 节电档不可用时换同一成员的其他可用模型
    members = chat_members(env.config, {"f1"})
    by = {m.id: m for m in members}
    assert by["V1"].model_id == "f1" and by["V1"].available
    assert not by["V2"].available


def test_mentions_and_everyone():
    env = Env()
    members = chat(env).members()
    assert [m.id for m in mentioned("@阿三 你好 @阿一", members)] == ["V3", "V1"]
    assert len(mentioned("@全员 早", members)) == len(members)
    assert mentioned("没人被点名", members) == []


def test_pick_without_mentions_is_one_or_two_available_members():
    env = Env()
    sizes = {len(chat(env, seed).pick("今天吃什么")) for seed in range(20)}
    assert sizes == {1, 2}
    assert [m.id for m in chat(env).pick("@阿二 你说呢")] == ["V2"]
    cap = env.config.roundtable.chat.max_mentioned
    assert len(chat(env).pick("@全员 早上好")) == min(cap, len(chat(env).members()))


def test_clean_reply():
    assert clean_reply("阿二：「好的」", "阿二", 100) == "好的"
    assert clean_reply("x" * 20, "阿二", 10) == "x" * 10 + "…"


async def test_replies_are_recorded_and_see_each_other():
    env = Env()
    room = chat(env)
    env.fake.queue("b1", "阿一：我先说")
    env.fake.queue("b2", "我接着说")
    msg, _ = room.post("@阿一 @阿二 大家好")
    speakers = room.pick("@阿一 @阿二 大家好")
    out = await room.replies(msg["id"], speakers)
    assert [(m["author"], m["text"], m["reply_to"]) for m in out] == [
        ("V1", "我先说", msg["id"]),
        ("V2", "我接着说", msg["id"]),
    ]
    first, second = env.fake.calls[-2:]
    assert "用户：@阿一 @阿二 大家好" in first.messages[-1].content
    assert "阿一：我先说" in second.messages[-1].content  # 后发言的人看得到前面的回应
    assert "<chat>" in second.messages[-1].content and "阿二" in second.messages[0].content
    assert first.params["max_tokens"] == env.config.roundtable.chat.max_tokens
    session = env.rt.repo.chat_session("main")
    rows = env.rt.repo.conn.execute(
        "SELECT step, model_id FROM calls WHERE session_id = ?", (session,)
    ).fetchall()
    assert [tuple(r) for r in rows] == [("chat", "b1"), ("chat", "b2")]
    # 群聊的隐藏会话不出现在历史列表
    assert all(s.id != session for s in env.rt.repo.list_sessions())


async def test_budget_exhausted_stops_everyone():
    env = Env()
    env.rt.budget.check = lambda estimate=0.0: SimpleNamespace(allowed=False)
    room = chat(env)
    msg, _ = room.post("@全员 在吗")
    out = await room.replies(msg["id"], room.pick("@全员 在吗"))
    assert out == [] and not env.fake.calls
    rows = env.rt.repo.chat_messages("main")
    assert rows[-1]["author"] == "system" and "预算用完" in rows[-1]["text"]


async def test_failed_member_is_skipped_with_a_note():
    env = Env()
    env.fake.queue("b1", *[ErrorKind.SERVER] * 8)
    env.fake.queue("b2", "我在")
    room = chat(env)
    msg, _ = room.post("@阿一 @阿二 在吗")
    out = await room.replies(msg["id"], room.pick("@阿一 @阿二 在吗"))
    assert [m["author"] for m in out] == ["V2"]
    texts = [r["text"] for r in env.rt.repo.chat_messages("main")]
    assert "阿一 暂时掉线了。" in texts
    assert room.typing == []


async def test_clear_deletes_messages_and_call_text_but_keeps_costs():
    env = Env()
    room = chat(env)
    msg, _ = room.post("@阿一 你好")
    await room.replies(msg["id"], room.pick("@阿一 你好"))
    before = env.rt.repo.total_spent()
    assert env.rt.repo.clear_chat("main") == 2
    assert env.rt.repo.chat_messages("main") == []
    session = env.rt.repo.chat_session("main")
    (row,) = env.rt.repo.conn.execute(
        "SELECT input, output FROM calls WHERE session_id = ?", (session,)
    ).fetchall()
    assert tuple(row) == ("[]", None)
    assert env.rt.repo.total_spent() == before


async def test_search_messages():
    env = Env()
    env.rt.repo.add_chat_message("main", "user", "周末去爬山")
    env.rt.repo.add_chat_message("main", "user", "今天下雨")
    found = env.rt.repo.chat_messages("main", query="爬山")
    assert [r["text"] for r in found] == ["周末去爬山"]


def test_everyone_words_ask_all_available_members():
    env = Env()
    n = len(chat(env).members())
    for text in ("所有人自我介绍一下自己", "大家觉得呢", "各位早"):
        assert len(chat(env).pick(text)) == n
    # 点了名就只叫被点名的人
    assert [m.id for m in chat(env).pick("@阿二 大家说的对吗")] == ["V2"]


async def test_empty_reply_is_explained_and_low_effort_params_are_used():
    env = Env()
    env.fake.queue("b1", "")
    room = chat(env)
    msg, _ = room.post("@阿一 在吗")
    assert await room.replies(msg["id"], room.pick("@阿一 在吗")) == []
    texts = [r["text"] for r in env.rt.repo.chat_messages("main")]
    assert "阿一 没有回复（回复是空的）。" in texts
    params = env.fake.calls[-1].params
    assert params["max_tokens"] == 1500 and params["reasoning"] == {"effort": "low"}


def test_members_without_nickname_use_the_art_name_then_the_vendor():
    env = Env()
    cfg = env.config
    config = SimpleNamespace(
        roundtable=cfg.roundtable,
        models=cfg.models,
        personas=SimpleNamespace(nicknames={"V1": "阿一"}),
    )
    ids = {m.id for m in cfg.models.models}
    names = {m.id: m.name for m in chat_members(config, ids, {"v1": "别名", "v2": "小二"})}
    assert names["V1"] == "阿一"  # personas 的昵称优先
    assert names["V2"] == "小二"  # 没有昵称时用成员形象里的称呼（厂商名不区分大小写）
    assert names["V3"] == "V3"  # 都没有时用厂商名
    # GroupChat 每次取成员时都问一次称呼来源
    gc = GroupChat(env.rt, names=lambda: {"v1": "别名"})
    assert {m.id: m.name for m in gc.members()}["V1"] == "阿一"
