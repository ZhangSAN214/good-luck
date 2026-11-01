"""P5 第二块：生活互聊、上网分享、朋友圈的跨消息重复（附和接龙、套同一个开头、反复提同一件事、
千篇一律的评论、没事也发朋友圈）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roundtable.core.chat import ROOM, ChatHub
from roundtable.core.life import LifeEngine, Plan
from roundtable.core.member.voice import Said, Voice, should_drop, voice_hits
from roundtable.core.memory import MemoryKeeper
from roundtable.core.moments import NOTHING, Moments
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import Env

NOW = datetime(2026, 10, 23, 20, 0, tzinfo=UTC)


def check(env: Env | None = None):
    return (env or Env()).config.roundtable.chat.engine.voice.check


def kinds(text: str, said: Said, c) -> set[str]:
    return {h.rule for h in voice_hits(text, said, Voice(), c)}


def test_rechew_same_thing_unless_asked():
    c = check()
    long = ("今晚又做了番茄牛腩，好香", "番茄牛腩还剩一点，明天热热")
    said = Said(long=long)
    assert kinds("周末想再炖一锅番茄牛腩", said, c) == {"rechew"}
    asked = Said(long=long, asked="你那个番茄牛腩怎么做的？")
    assert kinds("番茄牛腩就是先炒番茄再炖", asked, c) == set()
    assert kinds("周末想去楼下新开的书店", said, c) == set()


def test_same_opening_as_recent_shares():
    c = check()
    others = (("甲", "刚刷到一条新闻说火星有水"), ("乙", "刚刷到个帖子讲咖啡因"))
    assert "opening" in kinds("刚刷到一篇文章说熬夜伤肝", Said(others=others), c)
    assert "opening" not in kinds("熬夜伤肝这事又上新闻了", Said(others=others), c)


def test_quiet_kinds_drop_instead_of_rewrite():
    c = check()
    hits = voice_hits(
        "我也收盘子，天台见", Said(others=(("甲", "收完盘子去天台，天台见"),)), Voice(), c
    )
    assert should_drop(hits, "life", named=False)
    assert should_drop(hits, "comment", named=False)
    assert not should_drop(hits, "comment", named=True)  # 回你的评论：改写，不吞
    assert not should_drop(hits, "share", named=True)


# --- 接进生活、朋友圈、分享 ---------------------------------------------------------


def life(env: Env) -> LifeEngine:
    hub = ChatHub(env.rt)
    return LifeEngine(env.rt, hub, MemoryKeeper(env.rt, hub.everyone))


async def test_life_chat_stops_at_follow_the_leader():
    env = Env()
    eng = life(env)
    names = [m.id for m in eng.hub.everyone()][:2]
    replies = iter(["收完盘子我去天台，天台见", "我也收完盘子，天台见～", "第三句不该出现"])

    def reply(model, messages):
        if "现在你正和" in messages[0].content:
            return next(replies)
        return "嗯"

    env.fake._default = reply
    plan = Plan(NOW, "gather", "rooftop", names, "chat", topic="时间：星期五 20 点")
    said = await eng.write(plan, offline=False, now=NOW)
    assert said == 1  # 第二位只是「我也……天台见」：不发，这段收尾
    texts = [m["text"] for m in env.rt.repo.chat_messages(ROOM) if m["author"] in names]
    assert texts == ["收完盘子我去天台，天台见"]
    sent = [c for c in env.fake.calls if "现在你正和" in c.messages[0].content]
    assert "<voice>" in sent[0].messages[-1].content


def test_moments_skip_days_with_nothing_happening():
    env = Env()
    mo = Moments(env.rt, ChatHub(env.rt), lambda usd: True)
    member = mo.hub.everyone()[0]
    base = {"written": 0, "kind": "solo"}
    assert not mo.worth_posting(base | {"topic": f"地点：办公室\n今天的事：{NOTHING}"})
    assert mo.worth_posting(base | {"topic": "今天的事：整理了一份报告"})
    assert mo.worth_posting(base | {"kind": "gather", "topic": f"今天的事：{NOTHING}"})
    env.rt.repo.add_life_event(
        iso(NOW - timedelta(hours=1)), "solo", [member.id], place="office",
        topic=f"地点：办公室（待命）\n今天的事：{NOTHING}",
    )  # fmt: skip
    assert mo.event_for(member, NOW) is None


async def test_copycat_comment_is_not_posted():
    env = Env()
    mo = Moments(env.rt, ChatHub(env.rt), lambda usd: True)
    a, b, c = mo.hub.everyone()[:3]
    post = env.rt.repo.add_moment(a.id, "今天在天台看星星", meta={"life": True})
    env.rt.repo.add_moment_comment(post["id"], b.id, "看着就挺治愈的，好安静")
    env.fake._default = lambda model, messages: "看着挺治愈的，好安静啊"
    out = await mo.comment(c, env.rt.repo.moment(post["id"]))
    assert out == {}
    assert [x["author"] for x in env.rt.repo.moment(post["id"])["comments"]] == [b.id]


async def test_share_with_the_same_opening_is_rewritten_once():
    env = Env()
    hub = ChatHub(env.rt)
    eng = life(env)
    a, b, me = hub.everyone()[:3]
    for who, text in ((a, "刚刷到一条火星有水的新闻"), (b, "刚刷到个讲咖啡因的帖子")):
        env.rt.repo.add_chat_message(ROOM, who.id, text, meta={"life": True, "share": {"t": 1}})
    out = await eng.browse._self_check(
        me, "刚刷到一篇说熬夜伤肝的", {m.id: m.name for m in hub.everyone()}
    )
    rewrite = [c for c in env.fake.calls if "发出前自查" in c.messages[0].content]
    assert len(rewrite) == 1 and "开头「刚刷到」" in rewrite[0].messages[-1].content
    assert out  # 分享不会因为自查而不发
