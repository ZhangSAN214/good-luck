"""转发卡片（帖子 / 文章 / 视频）、转发消息、朋友圈转发与表情包、生成结果的接入口。"""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.chat import ChatHub, GroupChat
from roundtable.core.chat_actions import (
    DefaultPolicy,
    card_from_item,
    card_from_url,
    card_kind,
    card_text,
    clean_card,
    generated_item,
    is_bare_url,
)
from roundtable.core.chat_media import ChatMediaStore
from roundtable.core.moments import Moments
from roundtable.core.storage.repository import iso

from .orchestrator.conftest import Env

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
HOSTS = ["youtube.com", "bilibili.com"]
NOW = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)


async def no_sleep(_s: float) -> None:
    return None


def room(env: Env, name: str = "main") -> GroupChat:
    return GroupChat(env.rt, name, rng=random.Random(0), sleep=no_sleep)


# --- 卡片 ----------------------------------------------------------------------------


def test_card_kind_from_feed_and_host():
    assert card_kind("https://www.youtube.com/watch?v=1", "news", HOSTS) == "video"
    assert card_kind("https://m.bilibili.com/video/BV1", "blog", HOSTS) == "video"
    assert card_kind("https://example.com/a", "video", HOSTS) == "video"
    assert card_kind("https://forum.example.com/t/1", "forum", HOSTS) == "post"
    assert card_kind("https://notyoutube.com/x", "news", HOSTS) == "article"


def test_card_from_item_and_text():
    item = {
        "url": "https://example.com/a",
        "title": "新发现的系外行星",
        "feed": "科学新闻",
        "summary": "很长的摘要" * 30,
        "published": "2026-10-23T08:00:00+00:00",
        "unverified": False,
    }
    card = card_from_item(item, "news", HOSTS)
    assert card["kind"] == "article" and card["source"] == "科学新闻"
    assert len(card["summary"]) <= 61 and not card["unverified"]
    assert card_text(card) == "[链接] 新发现的系外行星"
    assert card_text({**card, "kind": "video"}) == "[视频] 新发现的系外行星"


def test_clean_card_rejects_bad_urls_and_fills_defaults():
    assert clean_card({"url": "javascript:alert(1)"}) is None
    assert clean_card("x") is None
    card = clean_card({"url": "https://example.com/p", "kind": "weird", "title": "<b>"})
    assert card["kind"] == "article" and card["title"] == "<b>" and card["unverified"]


def test_bare_url_card_uses_known_feed_item_without_network():
    assert is_bare_url(" https://example.com/x ") and not is_bare_url("看这个 https://a.com")
    card = card_from_url("https://www.youtube.com/watch?v=9", HOSTS)
    assert card["kind"] == "video" and card["title"] == "https://www.youtube.com/watch?v=9"
    known = {"url": "https://example.com/x", "title": "订阅源里的标题", "feed": "某博客"}
    assert card_from_url("https://example.com/x", HOSTS, known)["title"] == "订阅源里的标题"


# --- 聊天：粘贴链接、转发、接入口 ------------------------------------------------------


async def test_user_pasting_a_link_becomes_a_card_members_see_as_text():
    from roundtable.core.service import RoundtableService

    env = Env()
    env.rt.repo.add_web_item("科学新闻", "https://example.com/x", "火星上发现水", summary="摘要")
    svc = RoundtableService(env.rt)
    svc.chats.sleep = no_sleep
    await svc.chat_post("https://example.com/x", "main")
    await asyncio.gather(*list(svc._chat_tasks))
    msg = next(m for m in env.rt.repo.chat_messages("main") if m["author"] == "user")
    assert msg["text"] == "[链接] 火星上发现水"
    assert msg["meta"]["card"]["url"] == "https://example.com/x"
    assert any("[链接] 火星上发现水" in c.messages[-1].content for c in env.fake.calls)


async def test_forward_copies_media_and_card_to_another_room():
    from roundtable.core.service import RoundtableService, ServiceError

    env = Env()
    svc = RoundtableService(env.rt)
    svc.chats.sleep = no_sleep
    card = {"kind": "video", "title": "星空延时", "url": "https://www.youtube.com/watch?v=1"}
    src = svc.chats.room("main").send_as("V1", "好看", card=card)
    env.rt.repo.ensure_chat_room("dm:V2", kind="dm", title="阿二", members=["V2"])
    out = await svc.chat_forward(src["id"], "dm:V2")
    await asyncio.gather(*list(svc._chat_tasks))
    fwd = env.rt.repo.chat_message(out["id"])
    assert fwd["author"] == "user" and fwd["room"] == "dm:V2"
    assert fwd["meta"]["card"]["title"] == "星空延时"
    assert fwd["meta"]["forwarded"]["from"] == "阿一"
    assert out["speakers"] == ["阿二"]
    # 系统消息、撤回的不能转发；成员之间的私聊不能转进去
    pat = svc.chats.room("main").pat("V2")
    with pytest.raises(ServiceError):
        await svc.chat_forward(pat["id"], "main")
    env.rt.repo.ensure_chat_room("pair:V1+V2", kind="pair", title="x", members=["V1", "V2"])
    with pytest.raises(ServiceError):
        await svc.chat_forward(src["id"], "pair:V1+V2")


def test_send_as_entry_point_for_generated_stickers_and_cards(tmp_path):
    env = Env()
    r = room(env)
    store = ChatMediaStore(tmp_path)
    item = generated_item(store, PNG, "image/png", sticker=True)
    assert item["generated"] and item["sticker"] and store.load(item["id"])
    msg = r.send_as("V1", media=[item])
    assert msg["text"] == "[表情]" and msg["meta"]["auto_text"] is True
    assert msg["cost_usd"] == 0.0
    shared = r.send_as("V2", card={"url": "https://example.com/a", "title": "文章"})
    assert shared["text"] == "[链接] 文章"
    assert f"#{shared['id']} 阿二：[链接] 文章" in r.history({"V2": "阿二"}, numbered=True)
    with pytest.raises(ValueError):
        r.send_as("V9", "我不在")
    with pytest.raises(ValueError):
        r.send_as("V1")
    with pytest.raises(ValueError):
        r.send_as("V1", card={"url": "ftp://x"})
    with pytest.raises(ValueError, match="只能是图片"):
        generated_item(store, b"\x1aE\xdf\xa3" + b"\x00" * 32, "video/webm")


def test_policy_can_turn_moment_forward_off_and_it_is_reproducible():
    env = Env()
    policy = DefaultPolicy(env.rt)
    first = [policy.moment_forward("V1", str(i)) for i in range(40)]
    assert first == [policy.moment_forward("V1", str(i)) for i in range(40)]
    assert 0 < sum(first) < 40

    class Never(DefaultPolicy):
        rules = env.rt.config.roundtable.chat.actions.model_copy(update={"moment_forward": 0.0})

    assert not any(Never(env.rt).moment_forward("V1", str(i)) for i in range(40))


# --- 朋友圈 --------------------------------------------------------------------------


class Always(DefaultPolicy):
    def moment_forward(self, member, key):
        return True


def moment_env() -> tuple[Env, Moments]:
    env = Env()
    hub = ChatHub(env.rt)
    hub.policy = Always(env.rt)
    for m in hub.everyone():
        env.rt.repo.add_life_event(
            iso(NOW - timedelta(hours=2)), "solo", [m.id], place="rooftop", topic="看星星"
        )
    return env, Moments(env.rt, hub, lambda usd: True)


async def test_member_who_shared_today_posts_a_forward_card():
    env, mo = moment_env()
    env.rt.budget.now = lambda: NOW
    for i, m in enumerate(mo.hub.everyone()):  # 每位成员今天都分享过一条
        url = f"https://www.youtube.com/watch?v={i}"
        env.rt.repo.add_web_item("NASA 视频", url, "土星环", summary="s")
        env.rt.repo.mark_web_shared(env.rt.repo.web_item_by_url(url)["id"], m.id)
    await mo.tick(NOW)
    (post,) = env.rt.repo.moments()
    assert post["meta"]["card"]["kind"] == "video" and post["meta"]["card"]["title"] == "土星环"
    sent = [c for c in env.fake.calls if "想发一条朋友圈" in c.messages[0].content][0]
    assert "<forward>\n[视频] 土星环" in sent.messages[-1].content
    assert "[表情:" in sent.messages[0].content  # 表情包清单
    assert "[视频] 土星环" in Moments.post_text(post)


async def test_no_share_no_forward():
    env, mo = moment_env()
    await mo.tick(NOW)
    (post,) = env.rt.repo.moments()
    assert "card" not in post["meta"]
    sent = [c for c in env.fake.calls if "想发一条朋友圈" in c.messages[0].content][0]
    assert "<forward>\n（暂无）" in sent.messages[-1].content


def test_publish_entry_point_with_generated_image_and_card(tmp_path):
    env, mo = moment_env()
    item = generated_item(ChatMediaStore(tmp_path), PNG, "image/png")
    post = mo.publish("V1", "今天画的", media=[item])
    assert post["meta"]["media"][0]["generated"] and post["meta"]["caption"] == "今天画的"
    card = mo.publish("V2", card={"url": "https://example.com/z", "title": "好文"})
    assert card["text"] == "[链接] 好文" and card["meta"]["card"]["title"] == "好文"
    with pytest.raises(ValueError):
        mo.publish("V9", "x")
    with pytest.raises(ValueError):
        mo.publish("V1")
