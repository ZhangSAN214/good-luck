"""聊天：谁来回应、@ 人、调用记账、预算、掉线、一键清空；多个会话（生活群、工作群、私聊）。"""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from roundtable.core.chat import (
    ChatHub,
    GroupChat,
    chat_budget,
    chat_members,
    clean_reply,
    dm_room,
    mentioned,
)
from roundtable.core.config.schema import ChatBudgetRules
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


async def test_replies_are_recorded_and_think_independently():
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
    # 大家同时独立想：后发言的人看不到别人还没发出的回复
    assert "我先说" not in second.messages[-1].content
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


def test_default_rooms_and_dm_list():
    env = Env()
    hub = ChatHub(env.rt)
    rooms = hub.rooms()
    assert [(r["id"], r["kind"], r["title"]) for r in rooms[:2]] == [
        ("main", "group", "大家庭"),
        ("work", "work", "工作群"),
    ]
    # 每位成员都有一个私聊入口（还没聊过的也列出），标题是昵称
    dms = [r for r in rooms if r["kind"] == "dm"]
    assert [r["id"] for r in dms] == [dm_room(m.id) for m in hub.everyone()]
    assert dms[0]["title"] == "阿一" and dms[0]["unread"] == 0 and dms[0]["last"] is None


def test_pair_room_title_follows_current_names():
    """双人房标题按成员现名拼：建房时存的旧名（改名前）不再显示。"""
    env = Env()
    env.rt.repo.ensure_chat_room(
        "pair:V1+V2", kind="pair", title="旧名一 和 旧名二", members=["V1", "V2"], with_user=False
    )
    hub = ChatHub(env.rt)
    (pair,) = [r for r in hub.rooms() if r["kind"] == "pair"]
    assert pair["title"] == "阿一 和 阿二"
    assert hub.room("pair:V1+V2").title == "阿一 和 阿二"


def test_unknown_rooms_are_rejected():
    env = Env()
    hub = ChatHub(env.rt)
    with pytest.raises(KeyError):
        hub.room("nope")
    with pytest.raises(KeyError):
        hub.room(dm_room("没有这个人"))


async def test_dm_only_the_member_replies_with_the_dm_prompt():
    env = Env()
    hub = ChatHub(env.rt, rng=random.Random(0))
    dm = hub.room(dm_room("V2"))
    assert dm.kind == "dm" and [m.id for m in dm.members()] == ["V2"]
    # 写「大家」或 @ 别人，私聊里也只有对方回应
    for text in ("大家好", "@阿一 在吗", "@全员 早"):
        assert [m.id for m in dm.pick(text)] == ["V2"]
    msg, speakers = dm.post("最近怎么样")
    out = await dm.replies(msg["id"], speakers)
    assert [m["author"] for m in out] == ["V2"]
    system = env.fake.calls[-1].messages[0].content
    assert "私聊" in system and "阿二" in system
    # 调用挂在私聊自己的隐藏会话上，计入聊天预算；不进历史列表
    session = env.rt.repo.chat_session(dm_room("V2"))
    rows = env.rt.repo.conn.execute(
        "SELECT step FROM calls WHERE session_id = ?", (session,)
    ).fetchall()
    assert [r[0] for r in rows] == ["chat"]
    assert all(s.id != session for s in env.rt.repo.list_sessions())
    # 私聊的消息不会出现在群里
    assert [r["text"] for r in env.rt.repo.chat_messages("main")] == []


async def test_work_group_uses_its_own_prompt():
    env = Env()
    work = ChatHub(env.rt).room("work")
    msg, _ = work.post("@阿三 进度怎么样")
    await work.replies(msg["id"], work.pick("@阿三 进度怎么样"))
    system = env.fake.calls[-1].messages[0].content
    assert "工作群聊" in system and "交给办公室" in system


async def test_unread_counts_and_last_message():
    env = Env()
    hub = ChatHub(env.rt)
    repo = env.rt.repo
    repo.add_chat_message("main", "user", "我说一句")
    a = repo.add_chat_message("main", "V1", "回你一句")
    repo.add_chat_message("main", "system", "系统消息不算未读")
    main = next(r for r in hub.rooms() if r["id"] == "main")
    assert main["unread"] == 1
    assert main["last"]["name"] == "系统" and main["last"]["text"] == "系统消息不算未读"
    repo.mark_chat_read("main", a["id"])
    assert next(r for r in hub.rooms() if r["id"] == "main")["unread"] == 0


async def test_monthly_chat_budget_stops_replies():
    env = Env()
    hub = ChatHub(env.rt)
    dm = hub.room(dm_room("V1"))
    msg, speakers = dm.post("你好")
    await dm.replies(msg["id"], speakers)
    assert chat_budget(env.rt).spent_usd > 0  # 私聊的花费计入聊天预算

    # 上限比一条回应的预估还低：不再调用，在会话里说明
    rules = env.config.roundtable.chat
    tight = rules.model_copy(update={"budget": ChatBudgetRules(monthly_usd=0.001)})
    env = Env(rt_update={"chat": tight})
    dm = ChatHub(env.rt).room(dm_room("V1"))
    msg, speakers = dm.post("还在吗")
    assert await dm.replies(msg["id"], speakers) == []
    assert not env.fake.calls
    last = env.rt.repo.chat_messages(dm_room("V1"))[-1]
    assert last["author"] == "system" and "本月聊天预算（$0.00）" in last["text"]


def test_chat_budget_only_counts_chat_sessions():
    env = Env()
    repo = env.rt.repo
    hub = ChatHub(env.rt)
    hub.room("main")
    assert chat_budget(env.rt).spent_usd == 0
    assert chat_budget(env.rt).limit_usd == 5.0
    assert repo.spent_in_workflow("discussion", *_month(env)) == 0


def _month(env):
    from roundtable.core.budget.guard import month_window

    return month_window(env.rt.budget.now())


async def test_work_group_never_carries_the_pocket_card():
    """工作群不带人物卡和人设（2026-10-29 用户：工作与生活分开）；生活群照常带。"""
    from roundtable.core.persona import set_persona
    from roundtable.core.service import RoundtableService

    from .member.test_card import CARDS

    env = Env()
    svc = RoundtableService(env.rt)
    svc.member_profile_import(CARDS, {"甲": "V1", "乙": ""})
    set_persona(env.rt, "V1", {"personality": "旧人设标记"})
    for name in ("work", "main"):
        room = ChatHub(env.rt).room(name)
        env.fake.queue("b1", "收到")
        msg, _ = room.post("@阿一 在吗")
        await room.replies(msg["id"], room.pick("@阿一 在吗"))
    work, life = (json_text(c) for c in env.fake.calls[-2:])
    assert "阿甲" not in work and "旧人设标记" not in work
    assert "阿甲" in life


def json_text(call) -> str:
    return "\n".join(m.content for m in call.messages)


async def test_crisis_words_in_a_dm_reach_the_member_as_a_care_card():
    """危机应对（2026-10-30）：私聊里说出想不开的话，成员拿到的是关心卡（求助电话按主时区）；
    工作群里也一样（chat_work v4 的 <care>，不带人设），平常的工作消息不带。"""
    from roundtable.core.hometz import home_zone

    env = Env()
    home_zone(env.rt).set("Asia/Shanghai")
    dm = ChatHub(env.rt, rng=random.Random(0)).room(dm_room("V2"))
    msg, speakers = dm.post("我真的不想活了")
    await dm.replies(msg["id"], speakers)
    text = json_text(env.fake.calls[-1])
    assert "12356" in text and "不挽留" in text
    work = ChatHub(env.rt).room("work")
    msg, _ = work.post("@阿三 我不想活了")
    await work.replies(msg["id"], work.pick("@阿三 我不想活了"))
    assert "12356" in json_text(env.fake.calls[-1])
    env.rt.repo.set_setting("member_care", "")
    msg, _ = work.post("@阿三 进度怎么样")
    await work.replies(msg["id"], work.pick("@阿三 进度怎么样"))
    assert "12356" not in json_text(env.fake.calls[-1])
