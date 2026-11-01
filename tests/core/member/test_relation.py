"""P4 关系：和你的阶段、同事之间的亲近度全由程序按共同经历累积；只用于生活模式；
只影响生活里谁和谁聊；只有关系插件写关系。"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roundtable.core.chat import ChatHub
from roundtable.core.life import LifeEngine
from roundtable.core.member import WorkModeError
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.engine import MemberEngine
from roundtable.core.member.relation import USER, RelationPlugin
from roundtable.core.memory import MemoryKeeper
from roundtable.core.persona import chat_persona
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import MEDIUM, Env

T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
SRC = Path(__file__).resolve().parents[3] / "src" / "roundtable"


def plugin(env: Env, now: datetime = T0) -> tuple[RelationPlugin, EventBus]:
    env.rt.budget.now = lambda: now
    bus = EventBus(env.rt)
    return RelationPlugin(env.rt, bus, lambda: {"A": "阿一", "B": "阿二", "C": "阿三"}), bus


def say(bus: EventBus, at: datetime, room: str, who: str | None) -> None:
    """who = None 表示你说的话。"""
    data = {"room": room, "id": 0, "from_user": who is None, "life": False}
    bus.publish(Event("chat.message", at, who, data))


def chat_day(bus: EventBus, day: datetime, member: str, times: int, room: str = "") -> None:
    for i in range(times):
        at = day + timedelta(minutes=3 * i)
        say(bus, at, room or f"dm:{member}", None)
        say(bus, at + timedelta(minutes=1), room or f"dm:{member}", member)


# --- 和你 ---------------------------------------------------------------------------


def test_user_exchange_counts_once_per_message_and_only_in_window():
    env = Env()
    rel, bus = plugin(env)
    say(bus, T0, "dm:A", None)
    say(bus, T0 + timedelta(minutes=1), "dm:A", "A")
    say(bus, T0 + timedelta(minutes=2), "dm:A", "A")  # 拆成两条的回复只算一次
    u = rel.data("A")["user"]
    assert u["n"] == 1 and u["kinds"] == {"dm": 1}
    say(bus, T0 + timedelta(hours=1), "dm:A", None)
    say(bus, T0 + timedelta(hours=2), "dm:A", "A")  # 过了 30 分钟才回：不算一来一回
    assert rel.data("A")["user"]["n"] == 1
    assert rel.closeness("A", USER) > 0
    assert rel.closeness("B", USER) == 0


def test_group_reply_counts_less_and_work_group_not_at_all():
    env = Env()
    rel, bus = plugin(env)
    say(bus, T0, "main", None)
    say(bus, T0 + timedelta(minutes=1), "main", "A")
    say(bus, T0 + timedelta(minutes=1), "main", "B")
    assert rel.data("A")["user"]["pts"] == pytest.approx(0.5)
    assert rel.data("B")["user"]["kinds"] == {"group": 1}
    say(bus, T0 + timedelta(hours=1), "work", None)
    say(bus, T0 + timedelta(hours=1, minutes=1), "work", "C")
    assert rel.data("C")["user"] is None  # 工作群不算关系


def test_one_day_of_spam_never_makes_old_friends():
    env = Env()
    rel, bus = plugin(env)
    chat_day(bus, T0, "A", 120)
    u = rel.data("A")["user"]
    assert u["n"] == 120 and u["days"] == 1
    assert u["pts"] <= env.rt.config.roundtable.chat.engine.relation.user_daily_cap + 1e-9
    assert rel.stage("A") == "新邻居"  # 才认识一天


def test_stage_grows_with_real_days_and_never_drops():
    env = Env()
    rel, bus = plugin(env)
    for d in range(3):
        chat_day(bus, T0 + timedelta(days=d), "A", 6)
    assert rel.stage("A") == "熟悉"
    hist = rel.data("A")["stages"]
    assert [h["stage"] for h in hist] == ["熟悉"]
    for d in range(3, 50):
        chat_day(bus, T0 + timedelta(days=d), "A", 4)
    assert rel.stage("A") == "老朋友"
    close = rel.closeness("A", USER, T0 + timedelta(days=50))
    later = T0 + timedelta(days=400)
    env.rt.budget.now = lambda: later
    assert rel.stage("A") == "老朋友"  # 阶段只升不降
    assert rel.closeness("A", USER, later) < close / 2  # 亲近度一年不来往淡了
    assert "有阵子没聊了" in rel.line("A", later)


def test_line_is_life_only_and_capped_the_same_for_everyone():
    env = Env()
    rel, bus = plugin(env)
    assert rel.line("A") == ""  # 还没说过话：不带
    chat_day(bus, T0, "A", 2)
    line = rel.line("A")
    assert "新邻居" in line and "认识第 1 天" in line
    assert len(line) <= env.rt.config.roundtable.chat.engine.relation.context_chars
    with pytest.raises(WorkModeError):
        rel.line("A", mode="work")


# --- 同事之间 -------------------------------------------------------------------------


def test_life_events_build_symmetric_peer_bonds_and_views():
    env = Env()
    rel, bus = plugin(env)
    for d in range(4):
        at = T0 + timedelta(days=d)
        for m in ("A", "B"):
            data = {"kind": "gather", "place": "canteen", "members": ["A", "B"]}
            bus.publish(Event("life.event", at, m, data))
    for m in ("A", "C"):
        data = {"kind": "pair", "place": "", "members": ["A", "C"]}
        bus.publish(Event("life.event", T0, m, data))
    assert rel.data("A")["peers"]["B"]["n"] == rel.data("B")["peers"]["A"]["n"] == 4
    assert rel.closeness("A", "B") == pytest.approx(rel.closeness("B", "A"))
    assert rel.closeness("A", "B") > rel.closeness("A", "C") > rel.closeness("B", "C") == 0
    assert "常在食堂碰到（4 次）" in rel.view("A", "B")
    assert "私下聊过 1 次" in rel.view("C", "A")
    assert rel.view("B", "C") == ""
    s = rel.summary("A")
    assert [p["member"] for p in s["peers"]] == ["B", "C"]


def test_chat_back_and_forth_and_shared_work_count():
    env = Env()
    rel, bus = plugin(env)
    say(bus, T0, "main", "A")
    say(bus, T0 + timedelta(minutes=2), "main", "B")
    say(bus, T0 + timedelta(minutes=3), "main", "B")  # 自己接自己不算
    assert rel.data("A")["peers"]["B"]["kinds"] == {"chat": 1}
    for m in ("A", "B", "C", "A"):  # 同一场重复的事件只算一次
        bus.publish(Event("work.done", T0, m, {"session_id": "s1", "question": "q"}))
    assert rel.data("C")["peers"]["A"]["kinds"] == {"work": 1}
    assert rel.data("A")["peers"]["B"]["kinds"] == {"chat": 1, "work": 1}
    assert "一起干过 1 次活" in rel.view("B", "C")


def test_scene_line_names_bonds_between_present_members():
    env = Env()
    rel, bus = plugin(env)
    for m in ("A", "B"):
        data = {"kind": "pair", "place": "", "members": ["A", "B"]}
        bus.publish(Event("life.event", T0, m, data))
    assert rel.scene_line(["A", "B", "C"]).startswith("交情：阿一和阿二")
    assert rel.scene_line(["B", "C"]) == ""


def test_clear_starts_over():
    env = Env()
    rel, bus = plugin(env)
    chat_day(bus, T0, "A", 2)
    chat_day(bus, T0, "B", 2)
    assert rel.clear("A") == 1
    assert rel.data("A")["user"] is None and rel.data("B")["user"]
    assert rel.clear() == 1


# --- 只影响生活里谁和谁聊 ----------------------------------------------------------


def life_engine(env: Env) -> LifeEngine:
    hub = ChatHub(env.rt)
    return LifeEngine(env.rt, hub, MemoryKeeper(env.rt, hub.everyone))


def pairs(life: LifeEngine, end: datetime, days: int = 60) -> Counter:
    plans = life.plan(end - timedelta(days=days), end)
    return Counter(tuple(sorted(p.members)) for p in plans if p.kind == "pair")


def test_close_members_meet_more_often_in_life_but_strangers_still_do():
    env = Env()
    end = datetime(2026, 10, 23, 22, tzinfo=UTC)
    env.rt.budget.now = lambda: end
    life = life_engine(env)
    ids = [m.id for m in life.hub.everyone() if m.available]
    before = pairs(life, end)
    assert pairs(life, end) == before  # 没有关系时照旧可复现
    a, b = ids[0], ids[1]
    rel = RelationPlugin(env.rt)
    for d in range(30):
        for x, y in ((a, b), (b, a)):
            rel.add(x, y, end - timedelta(days=d), 4.0, "pair")
    after = pairs(life, end)
    key = tuple(sorted((a, b)))
    assert after[key] > before[key]
    assert len(after) > 1  # 别的组合也照样碰到
    plan = next(p for p in life.plan(end - timedelta(days=60), end) if set(p.members) == {a, b})
    assert "交情：" in plan.topic and "走得近" in plan.topic


# --- 工作与生活分开、唯一写入方、引擎装好 -------------------------------------


async def test_relation_never_reaches_work_prompts():
    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    rel = svc.engine.relation
    now = datetime.now(UTC)
    for m in svc.chats.everyone():
        svc.engine.profiles.change(m.id, {"identity": {"名字": m.name}}, author="user")
        rel.add(m.id, USER, now, 1.0, "dm")
        assert "认识第" in chat_persona(env.rt, m.id)  # 生活里带着
    sid = svc.create(MEDIUM, seed=3, workflow="discussion")
    async for _ in svc.events(sid):
        pass
    assert env.fake.calls
    sent = json.dumps([[x.content for x in c.messages] for c in env.fake.calls], ensure_ascii=False)
    assert "认识第" not in sent and "新邻居" not in sent


def test_only_relation_plugin_writes_relations():
    writer = re.compile(r"\.(save_relation_data|clear_relation_data|save_relation_tracker)\(")
    allowed = {SRC / "core" / "member" / "relation.py"}
    for f in SRC.rglob("*.py"):
        if writer.search(f.read_text(encoding="utf-8")):
            assert f in allowed, f
    keys = re.compile(r"[\"'](member_relation:|relation\.tracker)")
    for f in SRC.rglob("*.py"):
        if keys.search(f.read_text(encoding="utf-8")):
            assert f.name == "repository.py", f


def test_work_dispatch_code_never_reads_relations():
    pattern = re.compile(r"relation")
    for pkg in ("allocation", "routing", "office", "orchestrator", "steps"):
        for f in (SRC / "core" / pkg).rglob("*.py"):
            text = f.read_text(encoding="utf-8")
            assert not pattern.search(text.replace("relationships", "")), f


async def test_engine_tick_feeds_relation_from_real_chat():
    env = Env()
    svc = RoundtableService(env.rt)
    assert isinstance(svc.engine.relation, RelationPlugin)
    m = svc.chats.everyone()[0].id
    repo = env.rt.repo
    now = datetime.now(UTC)
    repo.set_setting("engine.last_tick", (now - timedelta(minutes=10)).isoformat())
    svc.chats.room(f"dm:{m}")  # 建好私聊
    repo.add_chat_message(f"dm:{m}", "user", "在吗", created_at=iso(now - timedelta(minutes=2)))
    repo.add_chat_message(f"dm:{m}", m, "在的", created_at=iso(now - timedelta(minutes=1)))
    await svc.engine.tick(now)
    got = svc.member_relation(m)
    assert got["user"]["n"] == 1 and got["user"]["stage"] == "新邻居"
    assert svc.member_relation_clear(m) == {"deleted": 1}


def test_same_rules_for_every_member():
    env = Env()
    engine = MemberEngine(env.rt, lambda: ["A", "B"])
    rel = engine.relation
    for m in ("A", "B"):
        rel.add(m, USER, T0, 1.0, "dm")
    env.rt.budget.now = lambda: T0
    assert rel.line("A") == rel.line("B")
