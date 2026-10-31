"""「第一次」纪念：你在私聊亲口说的「第一次……」记下来（同一件只记一次，群里说的不记）；
过一个月、一年那天意图卡里可能带一句，真说了才算提过、只提一次；不是那天、关掉都不提。"""

from __future__ import annotations

import random
from datetime import date, timedelta

from roundtable.core.chat import dm_room
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.firsts import Firsts, add_months

from .test_brain import NOON, setup


def rules(env, **update) -> None:
    cfg = env.rt.config.roundtable
    engine = cfg.chat.engine
    f = engine.firsts.model_copy(update=update)
    chat = cfg.chat.model_copy(update={"engine": engine.model_copy(update={"firsts": f})})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def test_add_months_keeps_the_day_or_the_month_end():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2026, 11, 2), 12) == date(2027, 11, 2)
    assert add_months(date(2026, 12, 5), 1) == date(2027, 1, 5)


def test_records_only_your_own_words_once():
    env, _ = setup()
    f = Firsts(env.rt)
    assert f.record("V1", "说实话这是我第一次跟人说这些。", NOON)
    assert not f.record("V1", "第一次跟人说这些", NOON)  # 同一件
    assert not f.record("V1", "今天好累", NOON)
    assert f.entries("V1")[0]["what"] == "跟人说这些"


def test_bus_only_dm_from_you():
    env, _ = setup()
    bus = EventBus(env.rt)
    Firsts(env.rt, bus)
    dm = env.rt.repo.add_chat_message(dm_room("V1"), "user", "我第一次自己做饭！")
    group = env.rt.repo.add_chat_message("main", "user", "我第一次去爬山")
    hers = env.rt.repo.add_chat_message(dm_room("V1"), "V1", "我第一次看这部剧")
    for row, room, mine in (
        (dm, dm_room("V1"), True),
        (group, "main", True),
        (hers, dm_room("V1"), False),
    ):
        bus.publish(
            Event("chat.message", NOON, None, {"room": room, "id": row["id"], "from_user": mine})
        )
    assert [e["what"] for e in Firsts(env.rt).entries("V1")] == ["自己做饭"]


def test_mentioned_on_the_month_day_only_once():
    env, brain = setup()
    rules(env, chance=1.0)
    f = Firsts(env.rt)
    f.record("V1", "我第一次自己做饭！", NOON)
    rng = random.Random(0)
    assert f.line("V1", NOON + timedelta(days=10), rng) is None
    day = NOON + timedelta(days=30)  # 11 月 2 日 → 12 月 2 日
    got = f.line("V1", day, rng)
    assert got is not None and "一个月前的今天" in got[1] and "自己做饭" in got[1]
    card = brain.intent("V1", last="在吗", named=True, group=False, at=day, seed="1")
    assert any(t.startswith("first:") for t in card.tags) and "只提这一次" in card.text
    brain.after(card, day)
    assert Firsts(env.rt).line("V1", day, rng) is None  # 提过了
    year = NOON.replace(year=NOON.year + 1)
    assert "一年前" in Firsts(env.rt).line("V1", year, rng)[1]


def test_not_said_means_not_marked_and_switched_off():
    env, _ = setup()
    rules(env, chance=1.0)
    f = Firsts(env.rt)
    f.record("V1", "我第一次自己做饭！", NOON)
    day = NOON + timedelta(days=30)
    f.after("V1", ())
    assert f.line("V1", day, random.Random(0)) is not None
    rules(env, enabled=False)
    assert Firsts(env.rt).line("V1", day, random.Random(0)) is None
