"""以后别再提：你开头就说「别提了 / 别再提考试了」的短话记下（具体的事记那件事，只说别提了就记
上一位刚说的那句）；私聊说的那位守、群里说的群里都守；聊天里带一句、念头池里那件事不再冒出来；
「可以提了」放开最近一件；长句、工作群、关掉都不动。"""

from __future__ import annotations

from roundtable.core.chat import dm_room, with_about_you
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.hush import Hush
from roundtable.core.memory import NONE

from .test_brain import NOON, setup


def say(env, room, author, text):
    return env.rt.repo.add_chat_message(room, author, text)


def test_named_topic_and_vague_one_take_the_last_member_line():
    env, _ = setup()
    h = Hush(env.rt)
    msg = say(env, dm_room("V1"), "user", "别再提考试了")
    assert h.heard(dm_room("V1"), msg["text"], msg["id"], NOON) == "hush"
    say(env, "main", "V2", "你上次面试咋样了")
    msg = say(env, "main", "user", "别提了")
    assert h.heard("main", msg["text"], msg["id"], NOON) == "hush"
    items = h.items("V1")
    assert [d["topic"] for d in items] == ["考试", ""] and items[1]["quote"] == "你上次面试咋样了"
    assert [d["topic"] for d in h.items("V3")] == [""]  # 私聊跟 V1 说的，V3 不知道


def test_long_sentences_and_lift():
    env, _ = setup()
    h = Hush(env.rt)
    long = say(env, "main", "user", "你不要说你不知道这件事啊我都跟你讲过好几遍了")
    assert h.heard("main", long["text"], long["id"], NOON) == ""
    msg = say(env, "main", "user", "以后别再说加班")
    h.heard("main", msg["text"], msg["id"], NOON)
    assert h.items("V1")[0]["topic"] == "加班"
    ok = say(env, "main", "user", "没事了可以提了")
    assert h.heard("main", ok["text"], ok["id"], NOON) == "lift" and h.items("V1") == []


def test_prompt_line_and_thoughts_are_held_back():
    env, brain = setup()
    h = Hush(env.rt)
    msg = say(env, dm_room("V1"), "user", "别再提考试了")
    h.heard(dm_room("V1"), msg["text"], msg["id"], NOON)
    memo = with_about_you(env.rt, NONE, "", member="V1")
    assert "【他说过别再提】考试" in memo and "他自己问起再说" in memo
    assert "考试" not in with_about_you(env.rt, NONE, "", member="V2")
    assert h.blocks("V1", "下周考试要加油") and not h.blocks("V1", "周末去爬山")


def test_bus_skips_work_room_and_switch_off():
    env, _ = setup()
    bus = EventBus(env.rt)
    Hush(env.rt, bus)
    work = say(env, "work", "user", "别提了")
    dm = say(env, dm_room("V1"), "user", "别再提前任")
    for row in (work, dm):
        bus.publish(
            Event(
                "chat.message",
                NOON,
                None,
                {"room": row["room"], "id": row["id"], "from_user": True},
            )
        )
    assert [d["topic"] for d in Hush(env.rt).items("V1")] == ["前任"]
    cfg = env.rt.config.roundtable
    engine = cfg.chat.engine
    off = engine.model_copy(update={"hush": engine.hush.model_copy(update={"enabled": False})})
    chat = cfg.chat.model_copy(update={"engine": off})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )
    assert Hush(env.rt).line("V1") == ""
