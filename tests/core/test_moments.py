"""朋友圈：按当天的生活事件发动态、点赞不花钱、评论有上限、不自评、回应用户、花费闸门。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ChatHub
from roundtable.core.moments import SKIPPED_KEY, Moments, similarity
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
    assert post["text"] == "今天在天台看星星，挺安静的。" and post["meta"]["life"] is True
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


FAKE_POST = "今天在天台看星星，挺安静的。"


def test_similarity_ignores_punctuation_and_catches_near_duplicates():
    assert similarity("今天在天台看星星，挺安静的。", "今天在天台看星星挺安静的") == 1
    near = similarity("今天在天台看星星，挺安静的。", "今晚在天台看星星，好安静啊！")
    limit = Env().config.roundtable.chat.moments.similarity_max
    assert near >= limit
    assert similarity("今天在天台看星星，挺安静的。", "下午去超市买了菜，晚上做红烧肉") < limit
    assert similarity("", "随便") == 0 and similarity("！！", "随便") == 0


def one_awake(env: Env, mo: Moments, n: int = 1) -> list:
    awake = sorted(mo.awake(NOW), key=lambda m: m.id)
    assert len(awake) >= n
    return awake[:n]


async def test_shared_event_is_posted_once_then_others_pick_another_or_skip():
    env = Env()
    mo = moments(env)
    a, b = one_awake(env, mo, 2)
    repo = env.rt.repo
    pair = repo.add_life_event(
        iso(NOW - timedelta(hours=1)), "pair", [a.id, b.id], topic="地点：天台\n在场：两人"
    )
    first = await mo.post(a, pair, mo.recent_posts(NOW))
    assert first["meta"] == {"life": True, "event": pair["id"]}
    # 同一件事别人已经发过：b 没有别的事就跳过，一分钱不花
    before = len(calls(env, "想发一条朋友圈"))
    assert mo.event_for(b, NOW) is None
    out = await mo.tick(NOW)
    assert out["posts"] == 0 and len(calls(env, "想发一条朋友圈")) == before
    # b 当天还有另一件事：改发那件
    solo = repo.add_life_event(
        iso(NOW - timedelta(hours=3)), "solo", [b.id], topic="地点：超市\n在场：一人"
    )
    assert mo.event_for(b, NOW)["id"] == solo["id"]


async def test_text_too_similar_to_recent_post_is_dropped_and_not_retried():
    env = Env()
    mo = moments(env)
    (a, b) = one_awake(env, mo, 2)
    repo = env.rt.repo
    repo.add_moment(b.id, FAKE_POST)  # 别人刚发过几乎一样的话
    event = repo.add_life_event(
        iso(NOW - timedelta(hours=1)), "solo", [a.id], topic="地点：天台\n在场：一人"
    )
    out = await mo.tick(NOW)
    assert out == out | {"posts": 0, "skipped": 1}
    assert [p["author"] for p in repo.moments()] == [b.id]
    assert repo.setting(SKIPPED_KEY) == f'["{a.id}:{event["id"]}"]'
    # 下次推进不再为这件事花钱
    before = len(calls(env, "想发一条朋友圈"))
    await mo.tick(NOW + timedelta(minutes=30))
    assert len(calls(env, "想发一条朋友圈")) == before


async def test_old_posts_outside_window_do_not_block():
    env = Env()
    mo = moments(env)
    (a,) = one_awake(env, mo)
    repo = env.rt.repo
    hours = env.config.roundtable.chat.moments.dedupe_hours
    old = iso(NOW - timedelta(hours=hours + 1))
    repo.add_moment("user", FAKE_POST, created_at=old)
    repo.add_life_event(iso(NOW - timedelta(hours=1)), "solo", [a.id], topic="地点：天台")
    out = await mo.tick(NOW)
    assert out["posts"] == 1 and out["skipped"] == 0


async def test_member_stops_writing_posts_after_daily_misses():
    env = Env()
    mo = moments(env)
    (a, b) = one_awake(env, mo, 2)
    repo = env.rt.repo
    repo.add_moment(b.id, FAKE_POST)  # 她写什么都和这条太像
    for hour in (1, 2, 3, 4):
        repo.add_life_event(
            iso(NOW - timedelta(hours=hour)), "solo", [a.id], topic=f"地点：天台 {hour}"
        )
    for minute in range(0, 60, 10):
        await mo.tick(NOW + timedelta(minutes=minute))
    limit = env.config.roundtable.chat.moments.post_misses
    assert len(calls(env, "想发一条朋友圈")) == limit
    assert mo.misses("posts") == {a.id: limit}


async def test_failed_comment_is_not_rewritten_and_moment_gives_up_after_misses():
    env = Env()
    mo = moments(env)

    async def nothing_new(*args, **kwargs):
        return ""  # 自查都没过：说的话和别人差不多

    mo._self_check = nothing_new
    awake = mo.awake(NOW)
    moment = env.rt.repo.add_moment("user", "今天下雨了", created_at=iso(NOW))
    for minute in range(0, 60, 10):
        out = await mo.tick(NOW + timedelta(minutes=minute))
        assert out["comments"] == 0
    limit = env.config.roundtable.chat.moments.comment_misses
    assert len(awake) > limit
    assert len(calls(env, "想在下面留一条评论")) == limit
    assert mo.misses("comments") == {str(moment["id"]): limit}
    assert len(mo.comment_skipped()) == limit


async def test_post_gets_a_photo_by_chance_with_daily_caps(tmp_path, monkeypatch):
    import io

    from PIL import Image

    from roundtable.core.config.schema import StickerCard
    from roundtable.core.member_stickers import MemberStickers, StickerBudgetExceeded

    env = Env()
    rt = env.rt
    rt.chat_media_dir = tmp_path / "media"
    members = [m.id for m in ChatHub(rt).everyone()]
    cards = {m: StickerCard(look="测试") for m in members}
    media = rt.config.roundtable.media
    photos = media.moment_photos.model_copy(update={"chance": 1.0, "per_day": 2})
    rt.config = rt.config.model_copy(
        update={
            "personas": rt.config.personas.model_copy(update={"sticker_cards": cards}),
            "roundtable": rt.config.roundtable.model_copy(
                update={"media": media.model_copy(update={"enabled": True, "moment_photos": photos})}
            ),
        }
    )
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "skyblue").save(buf, "JPEG")
    asked = []

    async def photo(self, member, scene, post, shot):
        asked.append((member, scene, post, shot))
        if len(asked) > 2:
            raise StickerBudgetExceeded("快超了")
        return buf.getvalue(), "image/jpeg"

    monkeypatch.setattr(MemberStickers, "photo", photo)
    seed_events(env)
    mo = moments(env)
    hub = ChatHub(rt)
    everyone = hub.everyone()
    for i, member in enumerate([everyone[0], everyone[0], everyone[1], everyone[2], everyone[3]]):
        event = {"id": 100 + i, "topic": f"地点：公寓楼天台（看星星）第 {i} 次"}
        await mo.post(member, event, [])
    posts = env.rt.repo.moments(limit=50)
    with_photo = [p for p in posts if p["meta"].get("photo")]
    # 每人每天最多 1 张、所有人每天最多 2 张；超了或画不出来照样发文字
    assert len(with_photo) == 2 and len(posts) > len(with_photo)
    assert len({p["author"] for p in with_photo}) == 2
    for p in with_photo:
        [item] = p["meta"]["media"]
        assert item["kind"] == "image" and item["generated"]
        assert p["text"].endswith("\n[图片]") and p["meta"]["caption"] in p["text"]
        assert (tmp_path / "media").exists()
    member, scene, post, shot = asked[0]
    assert "天台" in scene and shot in photos.shots
