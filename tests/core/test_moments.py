"""朋友圈：按当天的生活事件发动态、点赞不花钱、评论有上限、不自评、回应用户、花费闸门。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ChatHub
from roundtable.core.moments import Moments
from roundtable.core.storage.repository import iso

from .orchestrator.conftest import Env

NOW = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)


def moments(env: Env, allow=lambda usd: True) -> Moments:
    return Moments(env.rt, ChatHub(env.rt), allow)


def calls(env: Env, phrase: str) -> list:
    return [c for c in env.fake.calls if phrase in c.messages[0].content]


def seed_events(env: Env) -> None:
    for m in ChatHub(env.rt).everyone():
        env.rt.repo.add_life_event(
            iso(NOW - timedelta(hours=2)),
            "solo",
            [m.id],
            place="rooftop",
            topic=f"时间：星期五 18 点\n地点：公寓楼天台（看星星）\n在场：{m.name}",
        )


async def test_tick_posts_from_todays_events_likes_and_comments():
    env = Env()
    seed_events(env)
    mo = moments(env)
    out = await mo.tick(NOW)
    rules = env.config.roundtable.chat.moments
    assert out["posts"] == rules.posts_per_tick == 1
    (post,) = env.rt.repo.moments()
    assert post["text"] == "今天在天台看星星，挺安静的。" and post["meta"] == {"life": True}
    (call,) = calls(env, "想发一条朋友圈")
    assert "<scene>\n时间：星期五 18 点" in call.messages[-1].content
    assert out["comments"] <= rules.comments_per_tick
    assert post["author"] not in post["likes"]
    assert all(c["author"] != post["author"] for c in post["comments"])  # 不给自己评论
    # 点赞由种子决定：再推进一次结果不变，不重复点
    likes = list(post["likes"])
    await mo.tick(NOW + timedelta(minutes=30))
    again = env.rt.repo.moment(post["id"])
    assert again["likes"][: len(likes)] == likes and len(set(again["likes"])) == len(again["likes"])
    # 每位成员只主动评论一次
    authors = [c["author"] for c in again["comments"]]
    assert len(authors) == len(set(authors)) <= rules.max_comments
    # 每人每天最多一条：同一位不会再发
    posts = env.rt.repo.moments(limit=50)
    assert len({p["author"] for p in posts}) == len(posts)


async def test_no_events_no_post():
    env = Env()
    out = await moments(env).tick(NOW)
    assert out["posts"] == 0 and not calls(env, "想发一条朋友圈")


async def test_daily_cap_blocks_writing_but_likes_are_free():
    env = Env()
    seed_events(env)
    post = env.rt.repo.add_moment("user", "今天下雨")
    out = await moments(env, allow=lambda usd: False).tick(NOW)
    assert out["posts"] == 0 and out["comments"] == 0 and not env.fake.calls
    assert out["likes"] == len(env.rt.repo.moment(post["id"])["likes"])


async def test_members_respond_to_user_post_and_comments():
    env = Env()
    mo = moments(env)
    post = env.rt.repo.add_moment("user", "周末去爬山了")
    assert await mo.react_to_user_post(post["id"]) == 2
    first = env.rt.repo.moment(post["id"])["comments"][0]
    (call, *_) = calls(env, "想在下面留一条评论")
    assert "<post>\n周末去爬山了\n</post>" in call.messages[-1].content
    # 用户回复成员的评论：那位成员回一句，挂在用户的评论下
    mine = env.rt.repo.add_moment_comment(post["id"], "user", "谢谢", reply_to=first["id"])
    reply = await mo.reply_to_user_comment(mine["id"])
    assert reply["author"] == first["author"] and reply["reply_to"] == mine["id"]
    assert "谢谢" in calls(env, "想在下面留一条评论")[-1].messages[-1].content
    # 用户评论成员的动态：作者回一句
    theirs = env.rt.repo.add_moment("V3", "今天加班")
    c = env.rt.repo.add_moment_comment(theirs["id"], "user", "辛苦了")
    assert (await mo.reply_to_user_comment(c["id"]))["author"] == "V3"
    # 成员的评论不会触发「回应用户」
    member_c = env.rt.repo.add_moment_comment(theirs["id"], "V1", "加油")
    assert await mo.reply_to_user_comment(member_c["id"]) is None
