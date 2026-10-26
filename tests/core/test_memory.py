"""成员的分层记忆：按天整理、周 / 月滚动、长期档案、敏感内容不记、只用于聊天、受聊天预算约束。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.config.schema import ChatBudgetRules
from roundtable.core.memory import (
    LAST_DAY_KEY,
    MemoryKeeper,
    clean_memory,
    memory_context,
    month_id,
    week_id,
)

from .orchestrator.conftest import Env

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)  # 星期五，第 43 周


def keeper(env: Env) -> MemoryKeeper:
    hub = ChatHub(env.rt)
    return MemoryKeeper(env.rt, hub.everyone)


def say(env: Env, room: str, author: str, text: str, when: datetime) -> None:
    env.rt.repo.add_chat_message(room, author, text, created_at=when.isoformat())


def memory_calls(env: Env) -> list:
    return [c for c in env.fake.calls if "给自己看" in c.messages[0].content]


def test_clean_memory_drops_sensitive_lines_and_truncates():
    text = "周末爬山\n密码是 abc\n手机号 13800138000\n身份证在抽屉里\n喜欢喝茶"
    assert clean_memory(text, 100) == "周末爬山\n喜欢喝茶"
    assert clean_memory("字" * 20, 10) == "字" * 10 + "…"


def test_week_and_month_ids():
    assert week_id(date(2026, 10, 23)) == "2026-W43"
    assert month_id(date(2026, 10, 23)) == "2026-10"


async def test_catch_up_summarizes_only_days_with_chat_for_members_present():
    env = Env()
    ChatHub(env.rt).room(dm_room("V1"))
    day = datetime(2026, 10, 21, 9, 30, tzinfo=UTC)
    say(env, dm_room("V1"), "user", "周末去爬山吗", day)
    say(env, dm_room("V1"), "V1", "好呀", day)
    k = keeper(env)
    written = await k.catch_up(NOW)
    assert written == 1  # 只有阿一那天聊过；别人不调用模型
    (mem,) = env.rt.repo.memories("V1", layer="day")
    assert mem["period"] == "2026-10-21" and "爬山" in mem["text"]
    assert "密码" not in mem["text"]  # 模型写了敏感内容也会被丢掉
    log = memory_calls(env)[0].messages[-1].content
    assert "09:30 私聊 用户：周末去爬山吗" in log and "阿一：好呀" in log
    assert env.rt.repo.setting(LAST_DAY_KEY) == "2026-10-22"
    # 再整理一次不会重复调用
    calls = len(env.fake.calls)
    assert await k.catch_up(NOW) == 0 and len(env.fake.calls) == calls
    # 调用记在「记忆」隐藏会话上，计入聊天预算
    session = env.rt.repo.chat_session("memory")
    steps = env.rt.repo.conn.execute(
        "SELECT step FROM calls WHERE session_id = ?", (session,)
    ).fetchall()
    assert [r[0] for r in steps] == ["memory"]


async def test_dm_of_someone_else_is_not_in_the_log():
    env = Env()
    hub = ChatHub(env.rt)
    hub.room(dm_room("V2"))
    day = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    say(env, dm_room("V2"), "user", "只和阿二说的话", day)
    say(env, "main", "user", "群里的话", day)
    k = keeper(env)
    v1 = next(m for m in hub.everyone() if m.id == "V1")
    log = k.day_log(v1, date(2026, 10, 21), UTC)
    assert "群里的话" in log and "只和阿二说的话" not in log


async def test_finished_weeks_and_months_roll_up_and_update_profile():
    env = Env()
    repo = env.rt.repo
    repo.add_memory("V1", "day", "9 月 15 日的事", period="2026-09-15")
    repo.add_memory("V1", "day", "10 月 13 日的事", period="2026-10-13")
    repo.add_memory("V1", "day", "本周的事", period="2026-10-21")
    repo.set_setting(LAST_DAY_KEY, "2026-10-22")
    k = keeper(env)
    await k.catch_up(NOW)
    weeks = {m["period"]: m for m in repo.memories("V1", layer="week")}
    assert set(weeks) == {"2026-W38", "2026-W42"}  # 本周（W43）还没过完
    months = repo.memories("V1", layer="month")
    assert [m["period"] for m in months] == ["2026-09"]  # 10 月还没过完
    (profile,) = repo.memories("V1", layer="profile")
    assert "爬山" in profile["text"]


async def test_user_written_profile_is_kept():
    env = Env()
    repo = env.rt.repo
    old = repo.add_memory("V1", "profile", "我自己写的档案", source="user")
    k = keeper(env)
    member = next(m for m in ChatHub(env.rt).everyone() if m.id == "V1")
    assert await k.update_profile(member, "最近的事") is None
    assert repo.memories("V1", layer="profile") == [old]


def test_memory_context_order_and_limit():
    env = Env()
    repo = env.rt.repo
    assert memory_context(env.rt, "V1", 800) == "（暂无）"
    repo.add_memory("V1", "day", "前天", period="2026-10-21")
    repo.add_memory("V1", "day", "昨天", period="2026-10-22")
    repo.add_memory("V1", "profile", "档案")
    repo.add_memory("V1", "note", "用户说下周考试", source="user")
    text = memory_context(env.rt, "V1", 800)
    assert text.splitlines() == [
        "【长期档案】档案",
        "【那天（2026-10-22）】昨天",
        "【那天（2026-10-21）】前天",
        "【记下的】用户说下周考试",
    ]
    assert len(memory_context(env.rt, "V1", 10)) == 11


async def test_chat_prompt_carries_memory_and_persona():
    env = Env()
    env.rt.repo.add_memory("V1", "note", "用户说下周考试", source="user")
    env.rt.repo.set_member_persona("V1", {"personality": "爱较真", "interests": ["天文"]})
    dm = ChatHub(env.rt).room(dm_room("V1"))
    msg, speakers = dm.post("在吗")
    await dm.replies(msg["id"], speakers)
    user = env.fake.calls[-1].messages[-1].content
    assert "<memory>\n【记下的】用户说下周考试\n</memory>" in user
    assert "性格：爱较真" in user and "兴趣：天文" in user


async def test_memory_off_and_budget_block():
    env = Env()
    rules = env.config.roundtable.chat
    off = rules.model_copy(update={"memory": rules.memory.model_copy(update={"enabled": False})})
    env = Env(rt_update={"chat": off})
    say(env, "main", "user", "hi", datetime(2026, 10, 21, 9, tzinfo=UTC))
    assert await keeper(env).catch_up(NOW) == 0 and not env.fake.calls

    tight = rules.model_copy(update={"budget": ChatBudgetRules(monthly_usd=0.0001)})
    env = Env(rt_update={"chat": tight})
    say(env, "main", "user", "hi", datetime(2026, 10, 21, 9, tzinfo=UTC))
    assert await keeper(env).catch_up(NOW) == 0 and not env.fake.calls


def half_life_env() -> Env:
    rules = Env().config.roundtable.chat
    hl = rules.model_copy(
        update={"memory": rules.memory.model_copy(update={"recall": "half_life"})}
    )
    return Env(rt_update={"chat": hl})


async def test_half_life_day_items_quota_and_star():
    env = half_life_env()
    ChatHub(env.rt).room(dm_room("V1"))
    say(env, dm_room("V1"), "user", "周末去爬山吗", datetime(2026, 10, 21, 9, 30, tzinfo=UTC))
    assert await keeper(env).catch_up(NOW) == 1
    (mem,) = env.rt.repo.memories("V1", layer="day")
    lines = mem["text"].splitlines()
    quota = env.config.roundtable.chat.memory.daily_quota
    assert len(lines) == quota and lines[0].startswith("★")
    assert "密码" not in mem["text"]
    (call,) = [c for c in env.fake.calls if "挑出值得以后记得的事" in c.messages[0].content]
    assert f"最多 {quota} 行" in call.messages[0].content


async def test_half_life_recall_uses_conversation_and_records_why():
    env = half_life_env()
    repo = env.rt.repo
    star = repo.add_memory("V1", "day", "★用户说话喜欢简短", period="2026-08-01")
    noodle = repo.add_memory("V1", "day", "和用户去了一兰拉面店", period="2026-09-01")
    for i in range(8):
        repo.add_memory("V1", "day", f"整理了第{i}份文件", period=f"2026-10-1{i}")
    text = memory_context(env.rt, "V1", 800, "上次那家拉面店叫什么")
    assert "【长期】用户说话喜欢简短" in text and "一兰拉面店" in text
    assert len(text.splitlines()) == env.config.roundtable.chat.memory.inject_max
    assert repo.memory(star["id"])["recall_note"] == "标了重要，总是记得"
    got = repo.memory(noodle["id"])
    assert "『拉面店』" in got["recall_note"] and got["recalled_at"]
    # 聊天时用当前对话召回
    dm = ChatHub(env.rt).room(dm_room("V1"))
    msg, speakers = dm.post("还记得那家拉面店吗")
    await dm.replies(msg["id"], speakers)
    prompt = env.fake.calls[-1].messages[-1].content
    assert "一兰拉面店" in prompt


def test_layers_mode_is_default_and_does_not_record_recall():
    env = Env()
    assert env.config.roundtable.chat.memory.recall == "layers"
    m = env.rt.repo.add_memory("V1", "day", "昨天", period="2026-10-22")
    memory_context(env.rt, "V1", 800, "昨天")
    assert env.rt.repo.memory(m["id"])["recalled_at"] is None
