"""聊天里能发什么：表情包、引用、拆条、不接话、拍一拍、撤回；成员系统可替换策略。"""

from __future__ import annotations

import random
import re
from datetime import timedelta
from pathlib import Path

import pytest

from roundtable.core.chat import ChatHub, GroupChat
from roundtable.core.chat_actions import DefaultPolicy, Segment, clean_stickers, parse, sticker_list
from roundtable.core.config.loader import load_config

from .orchestrator.conftest import Env

ROOT = Path(__file__).resolve().parents[2]
STICKERS = {"团子": ["开心", "吃瓜"], "文字": ["6"]}


async def no_sleep(_s: float) -> None:
    return None


def room(env: Env, name: str = "main", seed: int = 0) -> GroupChat:
    return GroupChat(env.rt, name, rng=random.Random(seed), sleep=no_sleep)


# --- 解析 ------------------------------------------------------------------------


def test_parse_splits_on_blank_lines_and_merges_the_rest():
    p = parse("一\n\n二\n\n三\n\n四", stickers=STICKERS, max_segments=3, quotable=set())
    assert [s.text for s in p.segments] == ["一", "二", "三\n四"]
    # 单个换行不拆
    p = parse("第一行\n第二行", stickers=STICKERS, max_segments=3, quotable=set())
    assert [s.text for s in p.segments] == ["第一行\n第二行"]


def test_parse_quotes_only_known_ids_and_strips_stray_markers():
    p = parse("[引用#12] 对\n\n[引用#99]不对 [引用#3]", stickers={}, max_segments=3, quotable={12})
    assert p.segments == [Segment("对", 12), Segment("不对", None)]


def test_unknown_stickers_are_removed_known_ones_normalised():
    assert clean_stickers("好[表情：团子·吃瓜][表情:团子·不存在]", STICKERS) == "好[表情:团子·吃瓜]"
    p = parse("[表情:团子·没有]", stickers=STICKERS, max_segments=3, quotable=set())
    assert p.segments == [] and not p.no_reply
    assert sticker_list(STICKERS) == "[表情:团子·开心]、[表情:团子·吃瓜]、[表情:文字·6]"


def test_no_reply_only_when_allowed():
    skip = parse("[不回]", stickers={}, max_segments=3, quotable=set(), allow_no_reply=True)
    assert skip.no_reply
    p = parse("[不回] 好吧", stickers={}, max_segments=3, quotable=set())
    assert not p.no_reply and [s.text for s in p.segments] == ["好吧"]


def test_configured_stickers_match_the_web_sticker_panel():
    """成员能用的表情包名字必须是网页表情面板里有的（否则画不出来）。"""
    js = (ROOT / "web/js/stickers.js").read_text(encoding="utf-8")
    config = load_config(ROOT / "config")
    stickers = config.roundtable.chat.actions.stickers
    assert stickers, "chat.actions.stickers 不应为空"
    for pack, names in stickers.items():
        assert f"id: '{pack}'" in js or pack == "文字"
        for name in names:
            assert re.search(rf"\[\s*'{re.escape(name)}'", js), f"{pack}·{name} 不在 stickers.js 里"


# --- 群聊里的动作 ------------------------------------------------------------------


async def test_reply_is_split_into_bubbles_with_quote_and_cost_on_first():
    env = Env()
    r = room(env)
    msg, _ = r.post("@阿一 今天吃什么")
    env.fake.queue("b1", f"[引用#{msg['id']}] 火锅！\n\n[表情:团子·开心]")
    first = (await r.replies(msg["id"], r.pick("@阿一 今天吃什么")))[0]
    rows = [m for m in env.rt.repo.chat_messages("main") if m["author"] == "V1"]
    assert [m["text"] for m in rows] == ["火锅！", "[表情:团子·开心]"]
    quote = {"id": msg["id"], "author": "user", "text": "@阿一 今天吃什么"}
    assert rows[0]["meta"]["quote"] == quote
    assert first["id"] == rows[0]["id"]
    assert rows[1]["cost_usd"] == 0.0
    # 提示词里带上表情清单和编号的聊天记录
    sent = env.fake.calls[-1].messages
    assert "[表情:团子·开心]" in sent[0].content
    assert f"#{msg['id']} 用户：@阿一 今天吃什么" in sent[-1].content


async def test_member_may_skip_in_group_but_not_when_named():
    env = Env()
    r = room(env)
    msg, _ = r.post("今天天气不错")
    env.fake.queue("b1", "[不回]")
    v1 = next(m for m in r.members() if m.id == "V1")
    assert await r.replies(msg["id"], [v1]) == []
    assert all(m["author"] == "user" for m in env.rt.repo.chat_messages("main"))
    # 点名时不允许不接话：「[不回]」被去掉，剩下空回复会说明
    msg, _ = r.post("@阿一 你呢")
    env.fake.queue("b1", "[不回]")
    await r.replies(msg["id"], [v1])
    assert "没有回复" in env.rt.repo.chat_messages("main")[-1]["text"]


async def test_new_message_drops_unsent_bubbles_and_waiting_speakers():
    env = Env()
    r = room(env)
    msg, _ = r.post("@阿一 @阿二 说说")
    env.fake.queue("b1", "第一条\n\n第二条")

    async def user_types_again(_s: float) -> None:
        r.post("算了不聊这个")  # 第一位还在拆条时用户又发了一条

    r.sleep = user_types_again
    speakers = r.pick("@阿一 @阿二 说说")
    out = await r.replies(msg["id"], speakers, round_no=1)
    texts = [m["text"] for m in env.rt.repo.chat_messages("main") if m["author"] != "user"]
    assert texts == ["第一条"]  # 第二条气泡和阿二都作废了
    assert len(out) == 1


async def test_policy_can_be_replaced_by_the_member_system():
    env = Env()
    hub = ChatHub(env.rt)
    hub.sleep = no_sleep

    class OneBubble(DefaultPolicy):
        def max_segments(self, member, room_kind):
            return 1

        def drop_stale(self, room_kind):
            return False

    hub.policy = OneBubble(env.rt)
    r = hub.room("main")
    msg, _ = r.post("@阿一 说说")
    env.fake.queue("b1", "一\n\n二")
    await r.replies(msg["id"], r.pick("@阿一 说说"))
    assert env.rt.repo.chat_messages("main")[-1]["text"] == "一\n二"
    assert "最多 1 条" in env.fake.calls[-1].messages[0].content


async def test_same_action_prompt_for_every_member():
    """中立：同一会话里每位成员拿到的动作说明完全一样（只有名字不同）。"""
    env = Env()
    r = room(env)
    msg, _ = r.post("@全员 早")
    await r.replies(msg["id"], r.pick("@全员 早"))
    systems = {
        c.messages[0].content.split("能发的东西")[1].split("你的人物卡")[0]
        for c in env.fake.calls
        if "能发的东西" in c.messages[0].content  # 说出口前自查的改写调用不算
    }
    assert len(systems) == 1


# --- 拍一拍、撤回、引用 -------------------------------------------------------------


def test_pat_is_a_system_message_the_model_sees():
    env = Env()
    r = room(env)
    msg = r.pat("V2")
    assert msg["author"] == "system" and msg["text"] == "你拍了拍 阿二"
    assert msg["meta"]["pat"]["target"] == "V2"
    assert "（用户拍了拍 阿二）" in r.history({"V2": "阿二"}, numbered=True)
    assert r.pat("V1", actor="V3")["text"] == "阿三拍了拍 阿一"
    with pytest.raises(ValueError):
        r.pat("V9")


def test_recall_within_window_hides_message_from_members():
    env = Env()
    r = room(env)
    msg, _ = r.post("发错了")
    now = env.rt.budget.now()
    out = r.recall(msg["id"], now)
    assert out["text"] == "" and out["meta"]["recalled"]
    assert "发错了" not in r.history({}) and "发错了" not in r.history({}, numbered=True)
    late, _ = r.post("很久以前")
    with pytest.raises(ValueError, match="不能撤回"):
        r.recall(late["id"], now + timedelta(minutes=5))
    member_msg = env.rt.repo.add_chat_message("main", "V1", "成员说的")
    with pytest.raises(ValueError):
        r.recall(member_msg["id"], now)


def test_user_quote_is_stored_and_checked():
    env = Env()
    r = room(env)
    first, _ = r.post("原话")
    msg, _ = r.post("回你", quote=first["id"])
    assert msg["meta"]["quote"]["text"] == "原话"
    r.recall(first["id"], env.rt.budget.now())
    with pytest.raises(ValueError):
        r.post("再引用", quote=first["id"])


async def test_service_pat_replies_and_publishes_event():
    from roundtable.core.service import RoundtableService, ServiceError

    env = Env()
    svc = RoundtableService(env.rt)
    svc.chats.sleep = no_sleep
    seen = []
    svc.engine.bus.subscribe("chat.pat", seen.append)
    env.fake.queue("b2", "干嘛拍我 (・∀・)")
    out = await svc.chat_pat("V2", "main")
    assert out["speakers"] == ["阿二"]
    await asyncio_gather(svc)
    assert env.rt.repo.chat_messages("main")[-1]["text"] == "干嘛拍我 (・∀・)"
    assert seen and seen[0].member == "V2"
    env.rt.repo.ensure_chat_room("pair:V1+V2", kind="pair", title="x", members=["V1", "V2"])
    with pytest.raises(ServiceError):
        await svc.chat_pat("V1", "pair:V1+V2")


async def asyncio_gather(svc) -> None:
    import asyncio

    await asyncio.gather(*list(svc._chat_tasks))
