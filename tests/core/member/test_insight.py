"""P4 关系·对你的见解：每周按她自己的性格读「关于你」画像和最近和你的私聊，在上周的看法上
保留 / 加深 / 改观 / 放下，越熟写得越深；你随手回的一句由她自己琢磨；程序把关；8 位可以不同、
可以不同意；只进私聊和生活群，工作模式不挂载；结果只由关系插件存。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.insight import InsightKeeper, normalize
from roundtable.core.member import WorkModeError
from roundtable.core.member.about import AboutYou
from roundtable.core.member.relation import USER, RelationPlugin
from roundtable.core.service import RoundtableService, ServiceError

from ..orchestrator.conftest import MEDIUM, Env

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)  # 周一
WEEK = "2026-W43"
SRC = Path(__file__).resolve().parents[3] / "src" / "roundtable"
FACTS = ("在读研究生，学天文", "喜欢深夜写代码", "说自己不擅长和人打交道")


def is_insight(messages) -> bool:
    return "你对这位用户是怎么看的" in messages[0].content


def fact_ids(messages) -> dict[str, int]:
    block = messages[-1].content.split("<facts>")[1].split("</facts>")[0]
    out = {}
    for line in block.strip().splitlines():
        m = re.match(r"F(\d+)\. \[[^\]]+\] (.+)", line)
        if m:
            out[m.group(2)] = int(m.group(1))
    return out


def views_reply(messages, *, member_views=None) -> str:
    ids = fact_ids(messages)
    name = messages[0].content.split("你是「")[1].split("」")[0]
    views = (member_views or {}).get(name) or [
        {"text": "我觉得你挺能熬的，但别太晚睡", "facts": [ids["喜欢深夜写代码"]], "agree": True},
        {
            "text": "我认为你其实挺会和人聊的",
            "facts": [ids["说自己不擅长和人打交道"]],
            "agree": False,
        },
    ]
    return json.dumps({"views": views}, ensure_ascii=False)


def befriend(env: Env, days: int = 3, per_day: int = 5) -> None:
    """和每位聊够天数和来回（默认刚好到「熟悉」）。"""
    rel = RelationPlugin(env.rt)
    for m in ChatHub(env.rt).everyone():
        for d in range(days):
            for k in range(per_day):
                rel.add(m.id, USER, NOW - timedelta(days=d + 1, minutes=k), 1.0, "dm")


def prepare(env: Env, *, talk: bool = True, reply=None, facts=FACTS) -> list:
    env.rt.budget.now = lambda: NOW
    about = AboutYou(env.rt)
    for text in facts:
        about.add(text)
    if talk:
        befriend(env)
    seen: list = []
    base = env.reply

    def answer(model, messages):
        if is_insight(messages):
            seen.append(messages)
            return reply(messages) if reply else views_reply(messages)
        return base(model, messages)

    env.fake._default = answer
    return seen


def keeper(env: Env) -> InsightKeeper:
    return InsightKeeper(env.rt, ChatHub(env.rt).everyone)


def first(env: Env):
    return ChatHub(env.rt).everyone()[0]


# --- 把关 ---------------------------------------------------------------------------------


def test_normalize_always_says_i_think():
    assert normalize("我认为你很努力") == "我觉得你很努力"
    assert normalize("你很努力") == "我觉得你很努力"
    assert normalize("我觉得你很努力") == "我觉得你很努力"


async def test_weekly_views_pass_gate_and_are_stored_by_relation():
    env = Env()
    seen = prepare(env)
    k = keeper(env)
    m = first(env)
    got = await k.run(m, WEEK)
    assert got == {"views": 2, "skipped": 0}
    views = RelationPlugin(env.rt).views(m.id)
    assert [v["text"] for v in views] == [
        "我觉得你挺能熬的，但别太晚睡",
        "我觉得你其实挺会和人聊的",
    ]
    assert views[1]["agree"] is False
    assert RelationPlugin(env.rt).views_week(m.id) == WEEK
    # 材料：她自己、交情、画像（带编号和来源）、上周的看法
    user = seen[0][-1].content
    assert "<self>" in user and "熟悉" in user and "[你写的] 喜欢深夜写代码" in user
    assert views[0]["change"] == "新的" and views[0]["since"] == WEEK


async def test_gate_drops_uncited_rehash_and_sensitive_guesses():
    env = Env()

    def reply(messages):
        ids = fact_ids(messages)
        return json.dumps(
            {
                "views": [
                    {"text": "我觉得你很孤单", "facts": [], "agree": True},
                    {"text": "我觉得喜欢深夜写代码", "facts": [ids["喜欢深夜写代码"]]},
                    {"text": "我觉得你可能有抑郁症", "facts": [ids["喜欢深夜写代码"]]},
                    {"text": "我觉得你是个很认真的人", "facts": [ids["在读研究生，学天文"]]},
                ]
            },
            ensure_ascii=False,
        )

    prepare(env, reply=reply)
    m = first(env)
    await keeper(env).run(m, WEEK)
    rel = RelationPlugin(env.rt)
    assert [v["text"] for v in rel.views(m.id)] == ["我觉得你是个很认真的人"]
    note = rel.data(m.id)["views_log"][-1]["note"]
    assert "没写从哪来" in note and "只是把事实又说了一遍" in note
    assert "猜了不该猜的" in note


async def test_bad_format_retries_once_with_a_different_request():
    env = Env()
    replies = iter(["随便说说", None])
    seen = prepare(env, reply=lambda msgs: next(replies) or views_reply(msgs))
    m = first(env)
    got = await keeper(env).run(m, WEEK)
    assert got["views"] == 2 and len(seen) == 2
    assert seen[0][0].content != seen[1][0].content  # 不原样重试
    assert "没有按格式" in seen[1][0].content


async def test_too_little_to_think_about_skips_without_calling():
    env = Env()
    seen = prepare(env, talk=False)
    m = first(env)
    got = await keeper(env).run(m, WEEK)
    assert got == {"views": 0, "skipped": 1} and not seen
    assert RelationPlugin(env.rt).data(m.id)["views_log"][-1]["note"] == "还没和你说过话"


async def test_members_may_see_you_differently():
    env = Env()
    members = ChatHub(env.rt).everyone()
    a, b = members[0], members[1]

    def reply(messages):
        ids = fact_ids(messages)
        n = ids["说自己不擅长和人打交道"]
        return json.dumps(
            {
                "views": [
                    {"text": "我觉得你是慢热，不是不擅长", "facts": [n], "agree": False}
                    if a.name in messages[0].content
                    else {"text": "我觉得你更喜欢一个人安静待着", "facts": [n], "agree": True}
                ]
            },
            ensure_ascii=False,
        )

    prepare(env, reply=reply)
    k = keeper(env)
    await k.run(a, WEEK)
    await k.run(b, WEEK)
    rel = RelationPlugin(env.rt)
    assert rel.views(a.id)[0]["text"] != rel.views(b.id)[0]["text"]
    assert rel.views(a.id)[0]["agree"] is False and rel.views(b.id)[0]["agree"] is True


async def test_deleted_facts_take_their_views_out_of_chat():
    env = Env()
    prepare(env)
    m = first(env)
    await keeper(env).run(m, WEEK)
    rel = RelationPlugin(env.rt)
    assert "我觉得你挺能熬的" in rel.views_text(m.id)
    about = AboutYou(env.rt)
    night = next(f for f in about.facts() if f["text"] == "喜欢深夜写代码")
    about.delete(night["id"])
    assert "我觉得你挺能熬的" not in rel.views_text(m.id)
    insight = env.rt.config.roundtable.chat.engine.relation.insight
    limit = insight.at(rel.level(m.id)).context + len(insight.header) + 1
    assert len(rel.views_text(m.id)) <= limit
    with pytest.raises(WorkModeError):
        rel.views_text(m.id, mode="work")


# --- 会变、越熟越深、你的反应由她自己琢磨 ---------------------------------------------


NEXT = "2026-W44"


async def test_views_evolve_week_to_week_with_history_and_letting_go():
    env = Env()
    week2 = [False]

    def reply(messages):
        if not week2[0]:
            return views_reply(messages)
        user = messages[-1].content
        assert "P1. 我觉得你挺能熬的，但别太晚睡" in user and "（从 2026-W43 起这么想）" in user
        assert "TA 在网页上回你：「哈哈也不全是熬，是晚上安静」" in user
        return json.dumps(
            {
                "views": [
                    {
                        "text": "我觉得你是喜欢夜里的安静，不是硬熬",
                        "from": "P1",
                        "change": "改观",
                        "was": "以前以为你只是爱熬夜",
                        "facts": [fact_ids(messages)["喜欢深夜写代码"]],
                    }
                ]
            },
            ensure_ascii=False,
        )

    prepare(env, reply=reply)
    m = first(env)
    k = keeper(env)
    await k.run(m, WEEK)
    rel = RelationPlugin(env.rt)
    assert rel.reply_view(m.id, 0, "哈哈也不全是熬，是晚上安静")
    assert "你回过我：「哈哈也不全是熬，是晚上安静」" in rel.views_text(m.id)  # 聊天时她看得到
    week2[0] = True
    await k.run(m, NEXT)
    views = rel.views(m.id)
    assert len(views) == 1
    v = views[0]
    assert v["change"] == "改观" and v["since"] == WEEK and v["week"] == NEXT
    assert v["was"] == "以前以为你只是爱熬夜" and "reply" not in v
    assert v["history"] == [
        {
            "week": WEEK,
            "text": "我觉得你挺能熬的，但别太晚睡",
            "reply": "哈哈也不全是熬，是晚上安静",
        }
    ]
    changes = rel.data(m.id)["views_log"][-1]["changes"]
    assert changes[0] == "改观：以前以为你只是爱熬夜 → 我觉得你是喜欢夜里的安静，不是硬熬"
    assert changes[1] == "放下了：我觉得你其实挺会和人聊的"


async def test_kept_view_carries_its_sources_and_start_week():
    env = Env()
    week2 = [False]

    def reply(messages):
        if not week2[0]:
            return views_reply(messages)
        return json.dumps(
            {"views": [{"text": "我觉得你挺能熬的，但别太晚睡", "from": "P1", "change": "保留"}]},
            ensure_ascii=False,
        )

    prepare(env, reply=reply)
    m = first(env)
    await keeper(env).run(m, WEEK)
    week2[0] = True
    await keeper(env).run(m, NEXT)
    v = RelationPlugin(env.rt).views(m.id)[0]
    assert v["change"] == "保留" and v["since"] == WEEK and v["week"] == WEEK
    assert v["facts"] and v["history"] == []


async def test_moments_from_dm_are_material_and_can_be_the_only_source():
    env = Env()
    m = first(env)
    room = dm_room(m.id)
    env.rt.repo.ensure_chat_room(room, kind="dm", title=m.name)
    at = (NOW - timedelta(days=2)).isoformat()
    env.rt.repo.add_chat_message(room, USER, "你是不是觉得我很宅", created_at=at)
    env.rt.repo.add_chat_message(room, m.id, "我觉得你是慢热，不是宅", created_at=at)
    env.rt.repo.add_chat_message(room, USER, "哈哈被你看穿了", created_at=at)
    old = (NOW - timedelta(days=40)).isoformat()
    env.rt.repo.add_chat_message(room, USER, "很久以前的话", created_at=old)

    def reply(messages):
        user = messages[-1].content
        assert "M1. [" in user
        assert "TA：「你是不是觉得我很宅」 我：「我觉得你是慢热，不是宅」" in user
        assert "M2. [" in user and "哈哈被你看穿了" in user and "很久以前的话" not in user
        return json.dumps(
            {"views": [{"text": "我觉得你被说中时会先笑一下", "moments": ["M2"]}]},
            ensure_ascii=False,
        )

    prepare(env, reply=reply, facts=())  # 画像是空的：靠相处片段也能想
    got = await keeper(env).run(m, WEEK)
    assert got["views"] == 1
    rel = RelationPlugin(env.rt)
    v = rel.views(m.id)[0]
    assert v["quotes"] == ["哈哈被你看穿了"] and v["facts"] == []
    assert "被说中时会先笑一下" in rel.views_text(m.id)  # 没有画像条目也照常带


async def test_closer_means_more_and_longer_views():
    env = Env()
    long = "我觉得你说没事的时候其实是想一个人先消化一下再说，过两天自己会提起来"
    assert 30 < len(long) <= 60

    def reply(messages):
        ids = fact_ids(messages)
        n = ids["说自己不擅长和人打交道"]
        return json.dumps(
            {
                "views": [
                    {"text": long, "facts": [n]},
                    {"text": "我觉得你挺能熬的", "facts": [ids["喜欢深夜写代码"]]},
                    {"text": "我觉得你对星星是真喜欢", "facts": [ids["在读研究生，学天文"]]},
                ]
            },
            ensure_ascii=False,
        )

    seen = prepare(env, talk=False, reply=reply)
    rel = RelationPlugin(env.rt)
    a, b = ChatHub(env.rt).everyone()[:2]
    rel.add(a.id, USER, NOW - timedelta(days=1), 1.0, "dm")  # 新邻居
    for d in range(30):  # 老朋友：30 天、每天 6 次、跨 6 周以上
        for k in range(6):
            rel.add(b.id, USER, NOW - timedelta(days=d * 2 + 1, minutes=k), 1.0, "dm")
    assert rel.stage(a.id) == "新邻居" and rel.stage(b.id) == "老朋友"
    k = keeper(env)
    await k.run(a, WEEK)
    await k.run(b, WEEK)
    assert "写最多 1 句" in seen[0][0].content and "不超过 30 个字" in seen[0][0].content
    assert "写最多 5 句" in seen[1][0].content and "不超过 60 个字" in seen[1][0].content
    assert "刚认识" in seen[0][0].content and "老朋友了" in seen[1][0].content
    # 新邻居：长句超过 30 字被拦下，后面的只留 1 句；老朋友三句都留
    assert [v["text"] for v in rel.views(a.id)] == ["我觉得你挺能熬的"]
    assert "超过 30 字" in rel.data(a.id)["views_log"][-1]["note"]
    assert len(rel.views(b.id)) == 3 and rel.views(b.id)[0]["text"] == long


async def test_service_reply_and_unreply_view():
    env = Env()
    prepare(env)
    svc = RoundtableService(env.rt)
    svc._local_now = lambda: NOW
    m = first(env)
    await svc.member_relation_insight(m.id)
    got = svc.member_relation_reply_view(m.id, 1, "  其实  还好啦 ")
    assert got["views"][1]["reply"]["text"] == "其实 还好啦"
    assert got["depth"] == {"views": 3, "chars": 40}
    got = svc.member_relation_reply_view(m.id, 1, "")
    assert "reply" not in got["views"][1]
    with pytest.raises(ServiceError):
        svc.member_relation_reply_view(m.id, 9, "嗯")


# --- 每周一次、花费、接口 ---------------------------------------------------------------------


async def test_tick_runs_once_a_week_alongside_growth_and_respects_daily_limit():
    env = Env()
    seen = prepare(env)
    k = keeper(env)
    n = len(ChatHub(env.rt).everyone())
    k.allow = lambda usd: False
    assert (await k.tick(NOW))["members"] == 0 and not seen  # 额度不够：这周先不想
    k.allow = lambda usd: True
    assert (await k.tick(NOW))["members"] == n
    assert (await k.tick(NOW + timedelta(hours=3)))["members"] == 0  # 每周一次
    assert len(seen) == n
    sunday = datetime(2026, 10, 25, 12, tzinfo=UTC)
    assert k.due(sunday.date()).isoformat() == "2026-10-12"


async def test_service_think_now_drop_view_and_views_reach_dm_prompt():
    env = Env()
    prepare(env)
    svc = RoundtableService(env.rt)
    svc._local_now = lambda: NOW
    m = first(env)
    got = await svc.member_relation_insight(m.id)
    assert got["views_week"] == WEEK and len(got["views"]) == 2
    assert got["views"][0]["facts_text"] == ["喜欢深夜写代码"]
    with pytest.raises(ServiceError):
        await svc.member_relation_insight(m.id)  # 每周一次
    # 私聊里带着她的见解
    room = svc.chats.room(f"dm:{m.id}")
    msg, _ = room.post("在吗")
    await room.replies(msg["id"], [m])
    sent = [
        c
        for c in env.fake.calls
        if "我对你的看法" in c.messages[0].content + c.messages[-1].content
    ]
    assert sent
    got = svc.member_relation_drop_view(m.id, 0)
    assert len(got["views"]) == 1


async def test_views_never_reach_work_prompts():
    env = Env(confirm_threshold_usd=100.0)
    prepare(env)
    svc = RoundtableService(env.rt)
    for m in svc.chats.everyone():
        await keeper(env).run(m, WEEK)
    sid = svc.create(MEDIUM, seed=3, workflow="discussion")
    async for _ in svc.events(sid):
        pass
    sent = json.dumps(
        [[x.content for x in c.messages] for c in env.fake.calls if not is_insight(c.messages)],
        ensure_ascii=False,
    )
    assert "挺能熬的" not in sent and "我对你的看法" not in sent


def test_work_code_never_imports_insight_and_only_relation_writes():
    pattern = re.compile(r"roundtable\.core\.insight\b")
    for pkg in ("allocation", "routing", "office", "orchestrator", "steps", "prompts"):
        for f in (SRC / "core" / pkg).rglob("*.py"):
            assert not pattern.search(f.read_text(encoding="utf-8")), f
    text = (SRC / "core" / "insight.py").read_text(encoding="utf-8")
    assert "save_relation_data" not in text and "save_growth_data" not in text
    assert "save_about_you" not in text
