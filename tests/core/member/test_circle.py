"""圈外熟人名册：人物卡「人际关系」「身世」里和她自己说过的「我室友小林」记下来（带原话），
第一次说的为准、同一种关系最多两个名字、同事不算；对方提到时意图卡里带一句，免得说反；
总线上她自己的消息会自动记；关掉不动。"""

from __future__ import annotations

from datetime import timedelta

from roundtable.core.chat import dm_room
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.circle import Circle
from roundtable.core.member.profile import ProfileStore

from .test_brain import NOON, setup


def rules(env, **update) -> None:
    cfg = env.rt.config.roundtable
    engine = cfg.chat.engine
    c = engine.circle.model_copy(update=update)
    chat = cfg.chat.model_copy(update={"engine": engine.model_copy(update={"circle": c})})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def test_finds_relations_and_names_in_a_sentence():
    env, _ = setup()
    c = Circle(env.rt)
    got = c.found("昨天我室友小林又熬夜了。我妈打电话来催我回家！")
    assert got[0] == ("室友", "小林", "昨天我室友小林又熬夜了。")
    assert got[1][:2] == ("妈", "") and got[1][2].startswith("我妈打电话")
    assert c.found("我妈妈做饭超好吃")[0][:2] == ("妈妈", "")
    assert c.found("今天天气不错") == []


def test_card_entries_come_first_and_first_name_said_wins():
    env, _ = setup()
    ProfileStore(env.rt).change(
        "V1", {"identity": {"人际关系": "和我室友小林住了三年，她很爱熬夜。"}}, author="user"
    )
    c = Circle(env.rt)
    assert [(e["rel"], e["name"], e["source"]) for e in c.entries("V1")] == [
        ("室友", "小林", "card")
    ]
    assert c.learn("V1", "我室友小林今天又点外卖", NOON) == 0  # 已经有了
    assert c.learn("V1", "我室友小王把我零食吃了", NOON) == 1  # 第二个室友
    assert c.learn("V1", "我室友小周也在", NOON) == 0  # 最多两个，别越编越多
    assert c.learn("V1", "我妈说周末来看我", NOON) == 1
    assert c.learn("V1", "我妈又来了", NOON) == 0
    assert c.learn("V2", "我室友小周也在", NOON) == 1  # 各记各的


def test_colleagues_are_not_outsiders():
    env, _ = setup()
    nick = next(iter(env.rt.config.personas.nicknames.values()), "")
    c = Circle(env.rt)
    if nick.startswith(("小", "阿", "老")) and len(nick) == 2:
        assert c.learn("V1", f"我同学{nick}说的", NOON) == 0
    assert c.learn("V1", "我同学阿杰说的", NOON) == 1


def test_card_line_when_someone_mentions_them():
    env, brain = setup()
    c = Circle(env.rt)
    c.learn("V1", "我室友小林昨天又熬夜了", NOON)
    line = c.line("V1", "你室友最近还熬夜吗")
    assert "室友小林" in line and "我室友小林昨天又熬夜了" in line and "别前后说反" in line
    assert c.line("V1", "今天吃什么") == ""
    card = brain.intent("V1", last="小林最近咋样", named=True, group=False, at=NOON, seed="1")
    assert "circle" in card.tags and "室友小林" in card.text


def test_learns_from_her_own_messages_on_the_bus_only():
    env, _ = setup()
    bus = EventBus(env.rt)
    Circle(env.rt, bus)
    room = dm_room("V1")
    mine = env.rt.repo.add_chat_message(room, "V1", "我发小阿杰下周结婚")
    yours = env.rt.repo.add_chat_message(room, "user", "我室友小美也要结婚了")
    for row, from_user, author in ((mine, False, "V1"), (yours, True, None)):
        bus.publish(
            Event(
                "chat.message",
                NOON + timedelta(minutes=1),
                author,
                {"room": room, "id": row["id"], "from_user": from_user},
            )
        )
    names = [e["name"] for e in Circle(env.rt).entries("V1")]
    assert names == ["阿杰"]  # 你说的是你的室友，不记到她名下


def test_switched_off():
    env, _ = setup()
    Circle(env.rt).learn("V1", "我室友小林", NOON)
    rules(env, enabled=False)
    assert Circle(env.rt).line("V1", "你室友呢") == "" and Circle(env.rt).entries("V1") == []
