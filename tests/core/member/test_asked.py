"""被直接问到时先回答：意图卡不岔开去讲自己的事、不反问了事；问的是她自己的事才讲自己的事。"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from roundtable.core.member.brain import Brain

from .test_feel import AT, world


def intents(brain: Brain, last: str, *, group: bool = False, named: bool = True) -> Counter[str]:
    got: Counter[str] = Counter()
    for i in range(120):
        card = brain.intent(
            "V1", last=last, speaker=None, named=named, group=group,
            at=AT + timedelta(minutes=i), room="" if group else "dm:V1",
        )  # fmt: skip
        got[card.intent] += 1
        if "asked:other" in card.tags:
            assert brain.rules.asked.line in card.text
        if "asked:self" in card.tags:
            assert brain.rules.asked.self_line in card.text
        if "asked:judge" in card.tags:
            assert brain.rules.asked.judge_line in card.text
    return got


def test_a_direct_question_gets_answered_not_dodged():
    brain = Brain(world().rt)
    got = intents(brain, "你还记得我养的猫叫什么吗？")
    assert set(got) <= set(brain.rules.asked.intents)
    assert not {"share", "shift", "blurt", "ask", "vent"} & set(got)
    assert got["respond"] > got["joke"] > 0  # 认真答为主，也会开玩笑地答


def test_a_question_about_herself_may_be_answered_with_her_own_news():
    brain = Brain(world().rt)
    got = intents(brain, "最近怎么样？")
    assert got["share"] > 0 and not {"shift", "blurt", "ask"} & set(got)


def test_group_chatter_not_aimed_at_her_is_left_alone():
    env = world()
    brain = Brain(env.rt)
    card = brain.intent(
        "V1", last="有人知道食堂几点关门吗？", speaker=None, named=False, group=True, at=AT
    )
    assert card is None or not any(t.startswith("asked:") for t in card.tags)
    statement = brain.intent("V1", last="今天下雨了", speaker=None, named=True, group=False, at=AT)
    assert not any(t.startswith("asked:") for t in statement.tags)


def test_being_asked_to_judge_the_user_gets_a_reaction_not_a_profile():
    brain = Brain(world().rt)
    got = intents(brain, "你觉得我是个什么样的人？")
    assert set(got) <= set(brain.rules.asked.judge_intents) and got["joke"] > 0
    card = brain.intent("V1", last="你觉得我是个什么样的人？", named=True, group=False, at=AT)
    assert "asked:judge" in card.tags and "性格分析" in card.text
