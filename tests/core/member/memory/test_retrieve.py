"""召回：半衰期带保底、同一次对话不重复、联想、挂着的事；
记忆 100 → 10 万条注入字数不变；8 位同一预算。"""

from __future__ import annotations

import random
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.member.memory import LinkNet, MemoryStore, Recaller
from roundtable.core.member.memory.retrieve import freshness
from roundtable.core.memory import memory_context

from ...orchestrator.conftest import Env

NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
WORDS = [
    "图书馆",
    "拉面",
    "爬山",
    "考试",
    "论文",
    "电影",
    "游戏",
    "跑步",
    "朋友",
    "生日",
    "天文",
    "星星",
    "咖啡",
    "雨天",
    "地铁",
    "做饭",
]


def recall(env: Env, member: str, query: str, **kw):
    return Recaller(env.rt).recall(member, query, today=NOW.date(), now=NOW, **kw)


def test_freshness_has_a_floor():
    today = date(2026, 10, 29)
    assert freshness("2026-10-29", today, 30, 0.5) == 1.0
    assert freshness("2026-09-29", today, 30, 0.5) == pytest.approx(0.5, abs=0.02)
    assert freshness("2016-10-29", today, 30, 0.5) == 0.5  # 十年前的事也不低于保底
    assert freshness("2016-10-29", today, 30, 0.0) < 1e-30


def test_hanging_first_then_facts_and_relevant_things():
    env = Env()
    s = MemoryStore(env.rt)
    s.hang("V1", "周末爬山要带水", "2026-10-28")
    s.write("V1", "用户不吃香菜", day="2026-01-01", fact=True, importance=3)
    s.write("V1", "和用户去了一兰拉面店", day="2025-03-01")
    for i in range(20):
        env.rt.repo.add_memory_item("V1", f"整理文件{i}", day="2026-10-2" + str(i % 8))
    got = recall(env, "V1", "上次那家拉面店叫什么")
    lines = got.text.splitlines()
    assert lines[0] == "【挂着】周末爬山要带水"
    assert "【长期】用户不吃香菜" in lines and any("一兰拉面店" in x for x in lines)
    item = next(c for c in got.chosen if "拉面" in c.row["text"])
    assert "『拉面店』" in env.rt.repo.memory_item(item.id)["recall_note"]
    train = env.rt.repo.memory_train("V1")
    assert train[0]["kind"] == "recall" and train[0]["data"]["candidates"] == got.candidates


def test_same_conversation_does_not_repeat_but_another_room_does():
    env = Env()
    env.rt.repo.add_memory_item("V1", "和用户去了一兰拉面店", day="2026-10-01")
    first = recall(env, "V1", "拉面店", conversation="dm:V1")
    assert "一兰拉面店" in first.text
    again = recall(env, "V1", "拉面店", conversation="dm:V1")
    assert "一兰拉面店" not in again.text  # 她上次的回答已经在聊天记录里了
    other = recall(env, "V1", "拉面店", conversation="main")
    assert "一兰拉面店" in other.text
    later = Recaller(env.rt).recall(
        "V1", "拉面店", today=NOW.date(), now=NOW + timedelta(hours=3), conversation="dm:V1"
    )
    assert "一兰拉面店" in later.text  # 隔了很久算新的一次对话


def test_association_brings_in_a_linked_memory():
    env = Env()
    repo = env.rt.repo
    noodle = repo.add_memory_item("V1", "和用户去了一兰拉面店", day="2026-10-01")
    rain = repo.add_memory_item("V1", "那天突然下大雨没带伞", day="2026-10-01")
    for i in range(40):
        repo.add_memory_item("V1", f"整理文件{i}", day="2026-10-28")
    for _ in range(5):
        LinkNet(env.rt).strengthen("V1", [noodle["id"], rain["id"]])
    got = recall(env, "V1", "拉面店")
    assert "【想起】那天突然下大雨没带伞" in got.text.splitlines()
    assert repo.memory_item(rain["id"])["recall_note"] == "顺着联想想起"


def fill(env: Env, member: str, n: int, seed: int = 1) -> None:
    rng = random.Random(seed)
    env.rt.repo.add_memory_items_bulk(
        {
            "member": member,
            "text": "".join(rng.sample(WORDS, 3)) + f"那天的事第{i}次",
            "day": (date(2020, 1, 1) + timedelta(days=i % 2400)).isoformat(),
            "kind": "fact" if i % 50 == 0 else "episode",
            "importance": 3 if i % 500 == 0 else 1 + i % 2,
        }
        for i in range(n)
    )


def test_injected_chars_do_not_grow_from_100_to_100k_memories():
    budget = Env().config.roundtable.chat.memory.context_chars
    sizes = {}
    for n in (100, 100_000):
        env = Env()
        fill(env, "V1", n)
        assert env.rt.repo.count_memory_items("V1") == n
        start = time.perf_counter()
        got = recall(env, "V1", "上次一起去图书馆，然后吃了拉面")
        took = time.perf_counter() - start
        sizes[n] = (len(got.text), len(got.chosen), got.candidates)
        assert len(got.text) <= budget
        rules = env.config.roundtable.chat.memory
        assert got.candidates <= rules.candidates + rules.recent + 50  # 只看几百件候选
        assert took < 5  # 10 万条时一次召回本机约 0.1～0.2 秒
    small, big = sizes[100], sizes[100_000]
    assert small[1] == big[1]  # 每次带的件数一样
    assert abs(small[0] - big[0]) <= 60  # 字数不随总量涨（只差在每件长短）


async def test_eight_members_share_the_same_memory_budget():
    env = Env()
    members = ChatHub(env.rt).everyone()
    for k, m in enumerate(members):
        fill(env, m.id, 50 * (k + 1) ** 3, seed=k)  # 记忆量相差很大
    budget = env.config.roundtable.chat.memory.context_chars
    query = "图书馆 拉面 爬山 考试 论文 电影 游戏 跑步 朋友 生日"
    lengths = [len(memory_context(env.rt, m.id, budget, query)) for m in members]
    assert all(n <= budget for n in lengths)
    # 预算按配置统一取，和成员是谁无关；每位都按同样的件数上限挑
    rules = env.config.roundtable.chat.memory
    counts = [sum(not c.assoc for c in recall(env, m.id, query).chosen) for m in members]
    assert counts == [rules.inject_max] * len(members)
    # 私聊时提示词里的记忆也在同一预算内
    dm = ChatHub(env.rt).room(dm_room(members[0].id))
    msg, speakers = dm.post("还记得图书馆吗")
    await dm.replies(msg["id"], speakers)
    prompt = env.fake.calls[-1].messages[-1].content
    mem = prompt.split("<memory>\n")[1].split("\n</memory>")[0]
    assert len(mem) <= budget
