"""朋友圈：按当天的生活事件发动态、点赞不花钱、评论有上限、不自评、回应用户、花费闸门；
M2 说话像人（聊天原话、短、样子、不发、昵称）；M3 节奏像人（每天 0～2 条、晚上多、评论陆续到、
看亲疏、楼中楼 1 轮）。"""

from __future__ import annotations

import json
import random
from collections import Counter
from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ChatHub
from roundtable.core.moments import (
    PLAN_KEY,
    SKIPPED_KEY,
    Moments,
    one_name,
    post_slots,
    similarity,
    trim,
)
from roundtable.core.storage.repository import iso

from .orchestrator.conftest import Env

NOW = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)
FAKE_POST = "今天在天台看星星，挺安静的。"


def moments(env: Env, allow=lambda usd: True) -> Moments:
    return Moments(env.rt, ChatHub(env.rt), allow)


def calls(env: Env, phrase: str) -> list:
    return [c for c in env.fake.calls if phrase in c.messages[0].content]


def tune(env: Env, **rules) -> None:
    """改朋友圈规则（测试里让「到点」「几条评论」「多久到」可控）。"""
    rt = env.rt
    chat = rt.config.roundtable.chat
    new = chat.moments.model_copy(update=rules)
    rt.config = rt.config.model_copy(
        update={
            "roundtable": rt.config.roundtable.model_copy(
                update={"chat": chat.model_copy(update={"moments": new})}
            )
        }
    )


def due_now(env: Env, **more) -> None:
    """每位成员今天正好 1 条、19 点多抽到的时间（NOW 20:00 时在 3 小时窗口里）。"""
    tune(env, post_counts=[0, 1], post_hours={19: 1.0}, **more)


def answer(env: Env, *, post=None, comment=None) -> None:
    """换掉假模型对发动态 / 评论的回答（其余照旧）。"""
    orig = env.fake._default

    def reply(model, messages):
        system = messages[0].content
        if post is not None and "想发一条朋友圈" in system:
            return post(messages) if callable(post) else post
        if comment is not None and "想在下面留一条评论" in system:
            return comment(messages) if callable(comment) else comment
        return orig(model, messages)

    env.fake._default = reply


def seed_events(env: Env) -> None:
    for m in ChatHub(env.rt).everyone():
        env.rt.repo.add_life_event(
            iso(NOW - timedelta(hours=2)),
            "solo",
            [m.id],
            place="rooftop",
            topic=f"时间：星期五 18 点\n地点：公寓楼天台（看星星）\n在场：{m.name}",
        )


def one_awake(env: Env, mo: Moments, n: int = 1) -> list:
    awake = sorted(mo.awake(NOW), key=lambda m: m.id)
    assert len(awake) >= n
    return awake[:n]


# --- 发动态 ------------------------------------------------------------------------


async def test_tick_posts_when_due_and_comments_arrive_later():
    env = Env()
    due_now(env, comment_counts=[0, 0, 1], comment_delay_minutes=(5, 60))
    seed_events(env)
    lines = iter(["今天在天台看星星，挺安静的。", "楼顶风好大 天台看星星差点冻死"])
    quips = iter(["6", "带我", "笑死", "冷不冷", "哪颗最亮", "羡慕"])
    answer(
        env,
        post=lambda messages: next(lines, "又是天台看星星的一天"),
        comment=lambda messages: next(quips, "好"),
    )
    mo = moments(env)
    out = await mo.tick(NOW)
    rules = env.config.roundtable.chat.moments
    assert out["posts"] == rules.posts_per_tick == 2
    posts = env.rt.repo.moments()
    assert all("天台看星星" in p["text"] and p["meta"]["life"] for p in posts)
    assert all(p["meta"]["slot"] == 0 for p in posts)
    # 记在计划的时刻（19 点多），不是推进的那一刻
    assert all(iso(NOW - timedelta(hours=1)) <= p["created_at"] <= iso(NOW) for p in posts)
    (call, *_) = calls(env, "想发一条朋友圈")
    assert "<scene>\n时间：星期五 18 点" in call.messages[-1].content
    assert "<form>" in call.messages[-1].content
    # 评论不是发出同一分钟就到：这次推进只写到点的
    for p in posts:
        assert p["author"] not in p["likes"]
        assert all(c["created_at"] >= p["created_at"] for c in p["comments"])
    # 再推进几次：每条 2 条评论陆续到，不给自己评论，每人只评一次
    for minutes in (30, 60, 90, 120):
        await mo.tick(NOW + timedelta(minutes=minutes))
    for p in (env.rt.repo.moment(p["id"]) for p in posts):
        tops = [c for c in p["comments"] if not c["reply_to"]]
        assert len(tops) == 2 and all(c["author"] != p["author"] for c in tops)
        assert len({c["author"] for c in tops}) == 2
        gaps = [datetime.fromisoformat(c["created_at"]) for c in tops]
        start = datetime.fromisoformat(p["created_at"])
        assert all(timedelta(minutes=5) <= g - start <= timedelta(minutes=61) for g in gaps)
    # 点赞由种子决定：不重复点
    again = env.rt.repo.moment(posts[0]["id"])
    assert len(set(again["likes"])) == len(again["likes"])
    # 今天这一条发过了：同一位不会再发
    before = len(env.rt.repo.moments(limit=50))
    await mo.tick(NOW + timedelta(minutes=150))
    authors = [p["author"] for p in env.rt.repo.moments(limit=50)]
    assert len(authors) == len(set(authors)) and len(authors) >= before


async def test_not_due_no_post_and_no_events_no_post():
    env = Env()
    tune(env, post_counts=[1])  # 今天谁都不发
    seed_events(env)
    assert (await moments(env).tick(NOW))["posts"] == 0
    env2 = Env()
    due_now(env2)
    out = await moments(env2).tick(NOW)  # 到点了但今天没事
    assert out["posts"] == 0 and not calls(env2, "想发一条朋友圈")


async def test_daily_cap_blocks_writing_but_likes_are_free():
    env = Env()
    due_now(env)
    seed_events(env)
    post = env.rt.repo.add_moment("user", "今天下雨", created_at=iso(NOW - timedelta(hours=2)))
    out = await moments(env, allow=lambda usd: False).tick(NOW)
    assert out["posts"] == 0 and out["comments"] == 0 and not env.fake.calls
    assert out["likes"] == len(env.rt.repo.moment(post["id"])["likes"])


def test_post_slots_zero_to_two_a_day_mostly_in_the_evening():
    """M3：每天 0～2 条随机、平均不到 1 条；晚上（18～23 点）比白天多；同一天算几次都一样。"""
    rules = Env().config.roundtable.chat.moments
    members = [f"V{i}" for i in range(1, 9)]
    day0 = datetime(2026, 1, 1, tzinfo=UTC)
    counts, hours = Counter(), Counter()
    for d in range(365):
        day = day0 + timedelta(days=d)
        for m in members:
            slots = post_slots(rules, m, day)
            assert slots == post_slots(rules, m, day)
            assert slots == sorted(slots) and all(s.date() == day.date() for s in slots)
            counts[len(slots)] += 1
            hours.update(s.hour for s in slots)
    total = sum(counts.values())
    assert set(counts) == {0, 1, 2}
    mean = sum(n * c for n, c in counts.items()) / total
    assert 0.6 < mean < 1.0
    evening = sum(hours[h] for h in range(18, 24))
    daytime = sum(hours[h] for h in range(7, 18))
    assert evening > daytime and not any(hours[h] for h in range(0, 7))


async def test_post_window_passes_without_event_then_slot_is_gone():
    env = Env()
    due_now(env, post_window_hours=1)
    mo = moments(env)
    (a,) = one_awake(env, mo)
    assert mo.due_slot(a, NOW) is not None
    assert mo.due_slot(a, NOW + timedelta(hours=2)) is None  # 过了窗口：今天这条没了


# --- M2 说话像人 ----------------------------------------------------------------------


async def test_post_gets_the_chat_lines_of_a_written_event():
    env = Env()
    mo = moments(env)
    a, b = one_awake(env, mo, 2)
    repo = env.rt.repo
    room = f"pair:{a.id}:{b.id}"
    at = NOW - timedelta(hours=1)
    lines = ["食堂今天的红烧肉咸死了", "真的，我喝了三杯水", "明天去吃面吧"]
    for i, text in enumerate(lines):
        when = iso(at + timedelta(minutes=i))
        repo.add_chat_message(room, (a, b)[i % 2].id, text, meta={"life": True}, created_at=when)
    event = repo.add_life_event(
        iso(at), "pair", [a.id, b.id], topic="地点：私聊（聊天）\n今天的事：没有特别的事",
        room=room, written=True,
    )  # fmt: skip
    assert mo.talk_text(event).splitlines() == [
        f"{a.name}：{lines[0]}",
        f"{b.name}：{lines[1]}",
        f"{a.name}：{lines[2]}",
    ]
    await mo.post(a, event, [])
    (call,) = calls(env, "想发一条朋友圈")
    assert f"<talk>\n{a.name}：食堂今天的红烧肉咸死了" in call.messages[-1].content


async def test_dull_event_is_not_posted():
    """「待命」「放空」：没写成聊天、也没办活，不拿来发（用户那边 65% 的动态是这种）。"""
    env = Env()
    mo = moments(env)
    a, b = one_awake(env, mo, 2)
    env.rt.repo.add_life_event(
        iso(NOW - timedelta(hours=1)), "gather", [a.id, b.id],
        topic="地点：公寓楼办公室（待命）\n在场：两人\n今天的事：没有特别的事",
    )  # fmt: skip
    assert mo.event_for(a, NOW) is None
    worked = env.rt.repo.add_life_event(
        iso(NOW - timedelta(hours=2)), "gather", [a.id, b.id],
        topic="地点：公寓楼办公室（待命）\n在场：两人\n今天的事：写周报",
    )  # fmt: skip
    assert mo.event_for(a, NOW)["id"] == worked["id"]


async def test_long_answers_are_cut_at_a_sentence_end():
    env = Env()
    rules = env.config.roundtable.chat.moments
    answer(
        env,
        post="红烧肉咸死了！喝了三杯水还是渴，明天说什么也不去食堂了，打算去楼下那家面馆试试看。",
        comment="哈哈哈哈太真实了吧，我上次也是，后来我就再也不点那个菜了真的。",
    )
    mo = moments(env)
    a, b = one_awake(env, mo, 2)
    event = env.rt.repo.add_life_event(iso(NOW - timedelta(hours=1)), "solo", [a.id], topic="食堂")
    post = await mo.post(a, event, [])
    assert len(post["text"]) <= rules.post_max_chars + 4  # 收尾可能补个颜文字
    assert post["text"].startswith("红烧肉咸死了")
    c = await mo.comment(b, env.rt.repo.moment(post["id"]))
    assert len(c["text"]) <= rules.comment_max_chars + 4


def test_trim_prefers_sentence_then_clause_end():
    assert trim("短", 10) == "短"
    assert trim("今天好累。明天还要上班，烦死了", 8) == "今天好累。"
    assert trim("今天去超市买了好多东西，结果忘带钱包", 14) == "今天去超市买了好多东西"
    assert trim("一二三四五六七八九十", 4) == "一二三四"


async def test_skip_word_means_no_post_and_no_more_spending():
    env = Env()
    answer(env, post="不发", comment="（不发）")
    mo = moments(env)
    a, b = one_awake(env, mo, 2)
    event = env.rt.repo.add_life_event(iso(NOW - timedelta(hours=1)), "solo", [a.id], topic="天台")
    assert await mo.post(a, event, []) == {}
    assert env.rt.repo.moments() == []
    assert f"{a.id}:{event['id']}" in json.loads(env.rt.repo.setting(SKIPPED_KEY))
    assert mo.misses("posts") == {}  # 她自己说不发：不算写废
    theirs = env.rt.repo.add_moment(b.id, "下雨了")
    assert await mo.comment(a, theirs) == {}
    assert env.rt.repo.moment(theirs["id"])["comments"] == []
    assert mo.misses("comments") == {}


async def test_forward_can_be_just_the_card():
    env = Env()
    answer(env, post="不配字")
    mo = moments(env)
    (a,) = one_awake(env, mo)
    card = {"url": "https://example.com/x", "title": "猫咪学会开门", "kind": "blog"}
    card |= {"source": "少数派", "summary": ""}
    mo.forward_card = lambda member, event: card
    event = env.rt.repo.add_life_event(iso(NOW - timedelta(hours=1)), "solo", [a.id], topic="天台")
    post = await mo.post(a, event, [])
    assert post["text"] == "[链接] 猫咪学会开门" and post["meta"]["card"] == card
    (call,) = calls(env, "想发一条朋友圈")
    assert "直接甩" in call.messages[-1].content and "不配字" in call.messages[-1].content


def test_one_name_keeps_one_way_of_calling_each_person():
    names = {"V1": "小深", "V2": "阿克", "user": "用户"}
    text = "小深今天又加班，小深你行不行"
    assert one_name(text, names, {"V1": "深深"}) == "深深今天又加班，深深你行不行"
    assert one_name("阿克在吗", names, {"V1": "深深"}) == "阿克在吗"  # 没起昵称：不动
    assert one_name("深深好", names, {"V1": "深深"}) == "深深好"


async def test_nicknames_go_into_the_prompt_and_the_text():
    env = Env()
    mo = moments(env)
    a, b = one_awake(env, mo, 2)
    answer(env, comment=f"{b.name}也太会了")
    mo.nicks = lambda member: {b.id: "小可爱"} if member.id == a.id else {}
    post = env.rt.repo.add_moment(b.id, "今天学会做蛋挞了")
    c = await mo.comment(a, post)
    assert "小可爱" in c["text"] and b.name not in c["text"]
    (call,) = calls(env, "想在下面留一条评论")
    assert f"<calls>\n{b.name} → 小可爱\n</calls>" in call.messages[-1].content


# --- M3 评论：陆续到、看亲疏、楼中楼 ---------------------------------------------------------


def test_comment_plans_vary_zero_to_four_and_lean_on_closeness():
    env = Env()
    mo = moments(env)
    everyone = sorted(mo.hub.everyone(), key=lambda m: m.id)
    author, close = everyone[0], everyone[1]
    mo.closeness = lambda m, other: 1.0 if (m, other) == (close.id, author.id) else 0.0
    counts, picked, delays = Counter(), Counter(), []
    for i in range(400):
        moment = {"id": 10_000 + i, "author": author.id, "created_at": iso(NOW)}
        plan = mo.plan_comments(moment)
        counts[len(plan)] += 1
        picked.update(e["m"] for e in plan)
        assert author.id not in {e["m"] for e in plan}  # 不给自己评论
        assert [e["due"] for e in plan] == sorted(e["due"] for e in plan)
        delays += [(datetime.fromisoformat(e["due"]) - NOW).total_seconds() / 60 for e in plan]
        assert plan == mo.plan_comments(moment)  # 种子固定
    assert set(counts) == {0, 1, 2, 3, 4}
    others = [picked[m.id] for m in everyone[2:]]
    assert picked[close.id] > 2 * max(others)  # 越熟越常来评论
    low, high = env.config.roundtable.chat.moments.comment_delay_minutes
    assert all(low - 0.01 <= d <= high + 0.01 for d in delays)
    assert sum(d < 30 for d in delays) > len(delays) / 4  # 短的常见
    assert sum(d > 60 for d in delays) > len(delays) / 6  # 隔一两个小时的也有
    user = mo.plan_comments({"id": 1, "author": "user", "created_at": iso(NOW)})
    assert len(user) >= 1


async def test_author_replies_to_a_member_comment_once():
    """楼中楼：成员评论了，楼主（越熟越可能）回一句；回复不再被回（只回 1 轮）。"""
    env = Env()
    tune(env, comment_counts=[0, 1], comment_delay_minutes=(1, 2), reply_author=1.0)
    tune(env, reply_delay_minutes=(1, 2), reply_peer=0.0)
    mo = moments(env)
    a, _b = one_awake(env, mo, 2)
    post = env.rt.repo.add_moment(a.id, "今天学会做蛋挞了", created_at=iso(NOW))
    for minutes in (3, 6, 9, 12):
        await mo.tick(NOW + timedelta(minutes=minutes))
    comments = env.rt.repo.moment(post["id"])["comments"]
    first, reply = comments
    assert first["author"] != a.id and first["reply_to"] is None
    assert reply["author"] == a.id and reply["reply_to"] == first["id"]
    reply_call = calls(env, "想在下面留一条评论")[-1].messages[-1].content
    assert "在这条底下说" in reply_call
    plan = json.loads(env.rt.repo.setting(PLAN_KEY))[str(post["id"])]
    assert len(plan) == 2 and all(e.get("done") for e in plan)


async def test_peer_may_answer_under_a_user_post():
    env = Env()
    tune(env, comment_counts=[0, 1], reply_author=0.0, reply_peer=1.0)
    mo = moments(env)
    post = env.rt.repo.add_moment("user", "周末去爬山了", created_at=iso(NOW))
    first = env.rt.repo.add_moment_comment(post["id"], "V1", "哪座山", created_at=iso(NOW))
    follow = mo.plan_reply(env.rt.repo.moment(post["id"]), first)
    assert follow["to"] == first["id"] and follow["m"] not in ("V1", "user")
    reply = env.rt.repo.add_moment_comment(post["id"], "V2", "我也想去", reply_to=first["id"])
    assert mo.plan_reply(env.rt.repo.moment(post["id"]), reply) is None  # 只回 1 轮


async def test_asleep_at_the_due_time_means_no_comment():
    env = Env()
    tune(env, comment_counts=[0, 1], comment_delay_minutes=(1, 2), reply_author=0.0)
    tune(env, reply_peer=0.0)
    mo = moments(env)
    mo.asleep = lambda member, at: True
    post = env.rt.repo.add_moment("V1", "睡不着", created_at=iso(NOW))
    out = await mo.tick(NOW + timedelta(minutes=5))
    assert out["comments"] == 0 and env.rt.repo.moment(post["id"])["comments"] == []
    assert all(e.get("done") for e in json.loads(env.rt.repo.setting(PLAN_KEY))[str(post["id"])])


async def test_members_respond_to_user_post_and_comments():
    env = Env()
    tune(env, comment_counts=[0, 0, 0, 1], comment_delay_minutes=(10, 20))
    mo = moments(env)
    post = env.rt.repo.add_moment("user", "周末去爬山了", created_at=iso(NOW))
    assert await mo.react_to_user_post(post["id"]) == 1  # 最先来的那位马上评论
    first = env.rt.repo.moment(post["id"])["comments"][0]
    (call, *_) = calls(env, "想在下面留一条评论")
    assert "<post>\n周末去爬山了\n</post>" in call.messages[-1].content
    # 其余两位按计划陆续到
    await mo.tick(NOW + timedelta(minutes=30))
    tops = [c for c in env.rt.repo.moment(post["id"])["comments"] if not c["reply_to"]]
    assert len(tops) == 3 and len({c["author"] for c in tops}) == 3
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


# --- 去重、省钱 -------------------------------------------------------------------------


def test_similarity_ignores_punctuation_and_catches_near_duplicates():
    assert similarity("今天在天台看星星，挺安静的。", "今天在天台看星星挺安静的") == 1
    near = similarity("今天在天台看星星，挺安静的。", "今晚在天台看星星，好安静啊！")
    limit = Env().config.roundtable.chat.moments.similarity_max
    assert near >= limit
    assert similarity("今天在天台看星星，挺安静的。", "下午去超市买了菜，晚上做红烧肉") < limit
    assert similarity("", "随便") == 0 and similarity("！！", "随便") == 0


async def test_shared_event_is_posted_once_then_others_pick_another_or_skip():
    env = Env()
    due_now(env)
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
    due_now(env)
    mo = moments(env)
    (a, b) = one_awake(env, mo, 2)
    repo = env.rt.repo
    repo.add_moment(b.id, FAKE_POST, created_at=iso(NOW - timedelta(days=1, hours=1)))
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
    due_now(env)
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
    tune(env, post_counts=[0, 0, 1], post_hours={19: 1.0}, posts_per_member=2)
    mo = moments(env)
    (a, b) = one_awake(env, mo, 2)
    repo = env.rt.repo
    repo.add_moment(b.id, FAKE_POST, created_at=iso(NOW - timedelta(days=1, hours=1)))
    for hour in (1, 2, 3, 4):
        repo.add_life_event(
            iso(NOW - timedelta(hours=hour)), "solo", [a.id], topic=f"地点：天台 {hour}"
        )
    for minute in range(0, 60, 10):
        await mo.tick(NOW + timedelta(minutes=minute))
    limit = env.config.roundtable.chat.moments.post_misses
    assert (
        len([c for c in calls(env, "想发一条朋友圈") if a.name in c.messages[0].content]) == limit
    )
    assert mo.misses("posts")[a.id] == limit


async def test_failed_comment_is_not_rewritten_and_moment_gives_up_after_misses():
    env = Env()
    tune(env, comment_counts=[0, 0, 0, 0, 1], comment_delay_minutes=(1, 5))
    mo = moments(env)

    async def nothing_new(*args, **kwargs):
        return ""  # 自查都没过：说的话和别人差不多

    mo._self_check = nothing_new
    moment = env.rt.repo.add_moment("user", "今天下雨了", created_at=iso(NOW))
    for minute in range(0, 60, 10):
        out = await mo.tick(NOW + timedelta(minutes=minute))
        assert out["comments"] == 0
    limit = env.config.roundtable.chat.moments.comment_misses
    assert len(calls(env, "想在下面留一条评论")) == limit < 4
    assert mo.misses("comments") == {str(moment["id"]): limit}
    assert len(mo.comment_skipped()) == limit


async def test_post_gets_a_photo_by_chance_with_daily_caps(tmp_path, monkeypatch):
    import io

    from PIL import Image

    from roundtable.core.config.schema import StickerCard
    from roundtable.core.hometz import home_zone
    from roundtable.core.member_stickers import MemberStickers, StickerBudgetExceeded

    env = Env()
    rt = env.rt
    rt.chat_media_dir = tmp_path / "media"
    members = [m.id for m in ChatHub(rt).everyone()]
    cards = {m: StickerCard(look="测试") for m in members}
    media = rt.config.roundtable.media
    photos = media.moment_photos.model_copy(update={"chance": 1.0, "per_day": 2})
    media = media.model_copy(update={"enabled": True, "moment_photos": photos})
    rt.config = rt.config.model_copy(
        update={
            "personas": rt.config.personas.model_copy(update={"sticker_cards": cards}),
            "roundtable": rt.config.roundtable.model_copy(update={"media": media}),
        }
    )
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "skyblue").save(buf, "JPEG")
    asked = []

    async def photo(self, member, scene, post, shot, **kw):
        asked.append((member, scene, post, shot, kw))
        if len(asked) > 2:
            raise StickerBudgetExceeded("快超了")
        return buf.getvalue(), "image/jpeg"

    monkeypatch.setattr(MemberStickers, "photo", photo)
    seed_events(env)
    home_zone(rt).set("UTC")
    mo = moments(env)
    hub = ChatHub(rt)
    everyone = hub.everyone()
    for i, member in enumerate([everyone[0], everyone[0], everyone[1], everyone[2], everyone[3]]):
        event = {
            "id": 100 + i,
            "topic": f"地点：公寓楼天台（看星星）第 {i} 次",
            "place": "rooftop",
            "at": "2026-11-01T22:00:00+00:00",  # 主时区 UTC 的夜里
        }
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
    member, scene, post, shot, kw = asked[0]
    by_text = {s.text: s for s in photos.shots}
    assert "天台" in scene and shot in by_text
    # 她不入镜的拍法不传参考图；种子由事件和成员派生
    assert kw["refs"] == (by_text[shot].her != "none") and kw["seed"].startswith("100:")
    assert kw["place"] == "rooftop"  # 按地点附那间房的场景图
    # 吃标签的本地绘图模型用：拍法的 her 和标签版拍法
    assert kw["her"] == by_text[shot].her and kw["shot_tags"] == by_text[shot].tags
    # 摄影参数：拍法就是抽到的那种；夜里天台只会抽到夜里户外的光
    spec = kw["spec"]
    assert spec.shot == by_text[shot]
    if "lights" in spec.picks:
        assert spec.picks["lights"].name in {"blue_hour", "night_city", "flash"}
    assert kw["others"] == []  # 事件没写在场的人：没有室友
    # 会配图的那条，提示词里说了可以只发图
    first = calls(env, "想发一条朋友圈")[0].messages[-1].content
    assert "随手拍的照片" in first


async def test_photo_passes_housemates_and_skips_places_outside_the_building(tmp_path, monkeypatch):
    """11-02 用户：配图只画公寓里、只画公寓里的人；室友按她们的样子画。"""
    from roundtable.core.config.schema import StickerCard
    from roundtable.core.member_stickers import MemberStickers

    env = Env()
    rt = env.rt
    rt.chat_media_dir = tmp_path / "media"
    everyone = ChatHub(rt).everyone()
    cards = {m.id: StickerCard(look="测试") for m in everyone}
    media = rt.config.roundtable.media
    caps = {"chance": 1.0, "per_day": 9, "per_member_day": 9}
    photos = media.moment_photos.model_copy(update=caps)
    media = media.model_copy(update={"enabled": True, "moment_photos": photos})
    rt.config = rt.config.model_copy(
        update={
            "personas": rt.config.personas.model_copy(update={"sticker_cards": cards}),
            "roundtable": rt.config.roundtable.model_copy(update={"media": media}),
        }
    )
    asked = []

    async def photo(self, member, scene, post, shot, **kw):
        asked.append(kw)
        raise ValueError("不真画")

    monkeypatch.setattr(MemberStickers, "photo", photo)
    mo = moments(env)
    a, b = everyone[0], everyone[1]
    event = {"id": 7, "topic": "食堂", "place": "canteen", "at": None, "members": [a.id, b.id]}
    assert await mo.photo(a, event, "饭") is None
    assert asked[-1]["others"] == [(b.id, b.name)]
    for place in ("trip:beach", "group"):
        assert await mo.photo(a, {**event, "id": 8, "place": place}, "外面") is None
    assert len(asked) == 1  # 公寓楼外的事不配图


def test_photo_shots_mostly_show_her():
    """朋友圈是看她们的生活：默认拍法按权重抽，多数她本人入镜（11-01 用户：不要剩饭）。"""
    from roundtable.core.config.schema import MomentPhotoRules

    shots = MomentPhotoRules().shots
    rng = random.Random(1)
    picks = [rng.choices(shots, [s.weight for s in shots])[0].her for _ in range(2000)]
    assert 0.6 < picks.count("full") / len(picks) < 0.8
    assert picks.count("none") / len(picks) < 0.2


async def test_photo_only_post_without_a_photo_is_not_retried():
    env = Env()
    answer(env, post="不配字")
    mo = moments(env)
    (a,) = one_awake(env, mo)
    mo.photo_planned = lambda member, event: True

    async def no_photo(member, event, text):
        return None

    mo.photo = no_photo
    event = env.rt.repo.add_life_event(iso(NOW - timedelta(hours=1)), "solo", [a.id], topic="天台")
    assert await mo.post(a, event, []) == {}
    assert env.rt.repo.moments() == [] and mo.event_for(a, NOW) is None
