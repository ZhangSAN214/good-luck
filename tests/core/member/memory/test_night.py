"""夜间整理：每位成员每天一次调用，写第一人称日记 + 记忆件 + 挂着的事；心情只改语气和重要度；
生活只读自己的工作记录（匿名未揭晓不读）；不再另外滚动周 / 月。"""

from __future__ import annotations

from datetime import UTC, datetime

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.member.memory import MemoryStore
from roundtable.core.member.memory.night import apply, materials, mood_words, parse
from roundtable.core.memory import MemoryKeeper
from roundtable.core.service import RoundtableService

from ...orchestrator.conftest import MEDIUM, Env

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
DAY = datetime(2026, 10, 22, 9, 30, tzinfo=UTC)


def keeper(env: Env) -> MemoryKeeper:
    return MemoryKeeper(env.rt, ChatHub(env.rt).everyone)


def night_calls(env: Env) -> list:
    return [c for c in env.fake.calls if "今晚整理" in c.messages[0].content]


def chat(env: Env) -> None:
    ChatHub(env.rt).room(dm_room("V1"))
    env.rt.repo.add_chat_message(dm_room("V1"), "user", "周末去爬山吗", created_at=DAY.isoformat())


def test_parse_and_mood_words():
    assert parse("不是 JSON 的一段").diary == "不是 JSON 的一段"
    got = parse(
        '{"diary": "d", "items": [{"text": "a"}, "坏的"],'
        ' "hanging": {"add": ["x"], "done": ["2", "?"]}}'
    )
    assert got.items == [{"text": "a"}] and got.hang == ["x"] and got.done == [2]
    assert mood_words(0.0, 0.3) == "平静" and mood_words(-0.8, 0.3) == "很低落"


async def test_one_call_per_member_per_day_writes_diary_items_and_hanging():
    env = Env()
    chat(env)
    s = MemoryStore(env.rt)
    s.hang("V1", "给阿二回消息", "2026-10-20")
    k = keeper(env)
    assert await k.catch_up(NOW) == 1
    assert len(night_calls(env)) == 1  # 只有聊过天的阿一调用一次；不再滚动周 / 月和长期档案
    assert not [c for c in env.fake.calls if "压成一段小结" in c.messages[0].content]
    (diary,) = env.rt.repo.memories("V1", layer="day")
    assert (
        diary["period"] == "2026-10-22" and "爬山" in diary["text"] and "密码" not in diary["text"]
    )
    items = {r["text"]: r for r in env.rt.repo.memory_items("V1")}
    assert items["用户说周末只想去爬山，不想逛街"]["kind"] == "fact"
    assert items["用户说周末只想去爬山，不想逛街"]["importance"] == 3
    assert items["晚上看了会儿书"]["domain"] == "日常"  # 表外的领域用关键词兜底
    assert not any("密码" in t for t in items)
    assert [h["text"] for h in env.rt.repo.memory_hanging("V1")] == ["周末爬山要带水"]
    assert [h["text"] for h in env.rt.repo.memory_hanging("V1", "done")] == ["给阿二回消息"]
    system = night_calls(env)[0].messages[0].content
    assert "现在的心情：平静" in system and "最多 0 件" in system  # 心情平静不改旧记忆
    assert env.rt.repo.memory_digest("V1") is not None
    assert env.rt.repo.memory_train("V1")[0]["kind"] == "night"
    # 再整理一次不重复调用
    calls = len(env.fake.calls)
    assert await k.catch_up(NOW) == 0 and len(env.fake.calls) == calls


async def test_mood_only_retells_tone_and_importance_and_keeps_versions():
    env = Env()
    chat(env)
    old = env.rt.repo.add_memory_item("V1", "上周和阿三一起看了流星", day="2026-10-15")
    env.rt.repo.clock = lambda: "2026-10-22T10:00:00.000+00:00"
    env.rt.repo.mark_items_recalled({old["id"]: "提到了『流星』"})
    k = keeper(env)
    k.mood_of = lambda member: 0.8
    await k.catch_up(NOW)
    system = night_calls(env)[0].messages[0].content
    assert "现在的心情：很开心" in system and "1. 上周和阿三一起看了流星" in (
        night_calls(env)[0].messages[-1].content
    )
    got = env.rt.repo.memory_item(old["id"])
    assert got["text"] == "上周和阿三一起看了流星"  # 原文不改
    assert got["tone"] == "想起来还是挺开心的" and got["importance"] == 3
    fields = [r["field"] for r in env.rt.repo.memory_revisions(old["id"])]
    assert fields == ["tone", "importance"]


async def test_quiet_day_costs_nothing():
    env = Env()
    assert await keeper(env).catch_up(NOW) == 0 and not env.fake.calls


async def test_life_reads_own_work_records_but_not_unrevealed_anonymous():
    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    sid = svc.create(MEDIUM, seed=3, workflow="solo")
    async for _ in svc.events(sid):
        pass
    rows = env.rt.repo.work_records_between(
        datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC), ("solo",)
    )
    assert rows and rows[0]["session_id"] == sid and rows[0]["workflow"] == "solo"
    assert (
        env.rt.repo.work_records_between(
            datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC), ("office",)
        )
        == []
    )
    vendor = {m.id: m.vendor for m in env.config.models.models}
    member = vendor[rows[0]["model_id"]]
    hub = ChatHub(env.rt)
    me = next(m for m in hub.everyone() if m.id == member)
    k = MemoryKeeper(env.rt, hub.everyone)
    at = datetime.fromisoformat(rows[0]["at"]).astimezone(UTC)
    lines = k.work_lines(me, at.date(), UTC)
    assert rows[0]["revealed_at"] or not rows[0]["anonymous"]  # 个人作答结束时已揭晓
    title = " ".join(rows[0]["question"].split())[:40]
    assert lines == [f"在个人作答参加了「{title}」"]
    (item,) = env.rt.repo.memory_items(member)
    assert item["domain"] == "工作" and item["source"] == "work"
    assert k.work_lines(me, at.date(), UTC) == lines  # 同一场只记一次
    assert env.rt.repo.count_memory_items(member) == 1
    # 匿名且未揭晓：不读
    env.rt.repo.work_records_between = lambda start, end, wf: [
        {**rows[0], "session_id": "anon", "anonymous": 1, "revealed_at": None}
    ]
    assert k.work_lines(me, at.date(), UTC) == []
    assert env.rt.repo.memory_item_by_key(member, "work:anon") is None


def test_heading_only_diary_is_dropped():
    env = Env()
    mats = materials(env.rt, "V1", name="V1", day="2026-10-24", log="x", work=[], mood=0, drift=[])
    result = parse('{"diary": "10月24日回忆录：", "items": [{"text": "10月24日回忆录："}]}')
    counts = apply(env.rt, "V1", "2026-10-24", result, mats, clean=lambda t, n: t[:n])
    assert counts["diary"] == 0 and counts["new"] == 0
    assert env.rt.repo.memories("V1", layer="day") == []
    assert env.rt.repo.count_memory_items("V1") == 0
