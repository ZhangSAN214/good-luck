"""情境说法本：按场合（语境 / 原话 / 意图）和性格挑一条说话招式，只写逻辑不给句子；
同一招她几天内不重复、一周内已有两位用过别人不挑；情绪危机、她正委屈时不用；
你接她们某种话时说的短句会被记下，在乎你的成员顺手学；关掉不动；用自己的随机数不打乱别的区。"""

from __future__ import annotations

import random
from datetime import timedelta

from roundtable.core.chat import ChatHub, dm_room
from roundtable.core.config import load_config
from roundtable.core.member.brain import Brain
from roundtable.core.member.bus import Event
from roundtable.core.member.phrasebook import LEARNED_KEY, Phrasebook

from .test_brain import NOON, setup

FUNNY = {"外向": 5, "较真": 2, "温和": 2, "幽默": 5, "好奇": 3}
QUIET = {"外向": 1, "较真": 5, "温和": 5, "幽默": 1, "好奇": 3}


def rules(env, **update) -> None:
    cfg = env.rt.config.roundtable
    engine = cfg.chat.engine
    pb = engine.phrasebook.model_copy(update=update)
    chat = cfg.chat.model_copy(update={"engine": engine.model_copy(update={"phrasebook": pb})})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def pick(book, member="V1", *, last="", gist="", chosen="joke", traits=FUNNY, care=0.0, at=NOON):
    return book.line(
        member,
        last=last,
        gist=gist,
        chosen=chosen,
        traits=traits,
        care=care,
        hurt=False,
        at=at,
        rng=random.Random(0),
    )


def names(book, n=40, **kw) -> set[str]:
    out = set()
    for i in range(n):
        got = book.line(
            "V1",
            last=kw.get("last", ""),
            gist=kw.get("gist", ""),
            chosen=kw.get("chosen", "joke"),
            traits=kw.get("traits", FUNNY),
            care=0.0,
            hurt=False,
            at=NOON,
            rng=random.Random(i),
        )
        if got:
            out.add(got[0].split(":", 1)[1])
    return out


def test_entries_are_logic_not_fixed_sentences():
    pb = load_config().roundtable.chat.engine.phrasebook
    assert len(pb.entries) >= 8
    for e in pb.entries:
        assert "「" not in e.how  # 只写招式，不给可以照抄的句子
    for name in ("口是心非", "先立后破", "大词说小事", "多走一步", "把球踢出去"):
        assert name in {e.name for e in pb.entries}


def test_situation_and_personality_decide_which_moves_fit():
    env, _ = setup()
    rules(env, chance=1.0)
    book = Phrasebook(env.rt)
    funny = names(book, gist="funny", chosen="joke", traits=FUNNY)
    assert "演起来" in funny and "先立后破" in funny
    quiet = names(book, gist="funny", chosen="joke", traits=QUIET)
    assert "演起来" not in quiet and "先立后破" not in quiet  # 算出来不到 0：不用
    asked = names(book, last="你会不会做饭啊", gist="question", chosen="respond")
    assert "多走一步" in asked
    praised = names(book, last="你也太厉害了吧", chosen="respond")
    assert "被夸推回去" in praised
    assert pick(book, gist="crisis") is None
    assert (
        book.line(
            "V1",
            last="",
            gist="funny",
            chosen="joke",
            traits=FUNNY,
            care=0,
            hurt=True,
            at=NOON,
            rng=random.Random(0),
        )
        is None
    )  # 她正委屈


def test_not_twice_in_a_few_days_and_not_by_a_third_member_in_a_week():
    env, _ = setup()
    only = [e for e in env.rt.config.roundtable.chat.engine.phrasebook.entries if e.name == "嘴硬"]
    rules(env, chance=1.0, entries=only)
    book = Phrasebook(env.rt)
    got = pick(book, last="被我说中了吧", chosen="respond")
    assert got is not None and got[0] == "phrase:嘴硬" and "嘴硬" in got[1]
    book.after("V1", (got[0],), NOON)
    assert pick(book, last="被我说中了吧", chosen="respond", at=NOON + timedelta(days=1)) is None
    assert pick(book, last="被我说中了吧", chosen="respond", at=NOON + timedelta(days=4))
    book.after("V2", (got[0],), NOON)
    assert pick(book, "V3", last="被我说中了吧", chosen="respond") is None  # 两位用过了
    later = NOON + timedelta(days=8)
    assert pick(book, "V3", last="被我说中了吧", chosen="respond", at=later)


async def test_she_learns_what_you_say_in_that_kind_of_moment():
    env, _ = setup()
    rules(env, chance=1.0, entries=[])
    dm = ChatHub(env.rt).room(dm_room("V1"))
    book = Phrasebook(env.rt)
    for i in range(2):
        env.rt.repo.add_chat_message(dm_room("V1"), "V1", "哈哈哈笑死我了")
        msg, _ = dm.post("绷不住了")
        book.on_event(
            Event(
                kind="chat.message",
                member=None,
                at=NOON + timedelta(minutes=i),
                data={"room": dm_room("V1"), "id": msg["id"], "from_user": True},
            )
        )
    assert book.learned("funny", NOON) == ["绷不住了"]
    assert env.rt.repo.setting(LEARNED_KEY)
    assert pick(book, gist="funny", chosen="joke", care=0.1) is None  # 不在乎你：不学
    got = pick(book, gist="funny", chosen="joke", care=0.9)
    assert got is not None and "绷不住了" in got[1]
    assert book.learned("funny", NOON + timedelta(days=40)) == []  # 一个月不说就忘了


def test_switched_off_and_brain_card_uses_its_own_random():
    env, brain = setup()
    on = brain.intent("V1", last="哈哈哈笑死", named=True, group=False, at=NOON, seed="7")
    rules(env, enabled=False)
    off = Brain(env.rt).intent("V1", last="哈哈哈笑死", named=True, group=False, at=NOON, seed="7")
    assert on.intent == off.intent
    assert not any(t.startswith("phrase:") for t in off.tags)
    assert off.text in on.text  # 开了只是多一句


def test_brain_card_carries_a_move_and_marks_it_used():
    env, brain = setup()
    rules(env, chance=1.0)
    for seed in range(30):
        c = brain.intent(
            "V1", last="你也太厉害了吧", named=True, group=False, at=NOON, seed=str(seed)
        )
        tags = [t for t in c.tags if t.startswith("phrase:")]
        if tags:
            assert "这会儿可以用一招" in c.text or "有人爱说" in c.text
            brain.after(c, NOON)
            assert Phrasebook(env.rt)._taken(tags[0][7:], "V1", NOON)[0]
            return
    raise AssertionError("30 张卡都没提招式")


def test_later_speakers_avoid_the_move_someone_just_used():
    env, _ = setup()
    only = [e for e in env.rt.config.roundtable.chat.engine.phrasebook.entries if e.name == "嘴硬"]
    rules(env, chance=1.0, entries=only)
    book = Phrasebook(env.rt)
    book.after("V2", ("phrase:嘴硬",), NOON)
    soon = NOON + timedelta(minutes=5)
    assert pick(book, "V3", last="被我说中了吧", chosen="respond", at=soon) is None
    later = NOON + timedelta(minutes=20)
    assert pick(book, "V3", last="被我说中了吧", chosen="respond", at=later)  # 只有一位用过
    names_ = {e.name for e in load_config().roundtable.chat.engine.phrasebook.entries}
    assert {"换个壳", "补一刀"} <= names_


def test_first_meeting_always_gets_the_no_resume_hint(first_meeting):
    env, brain = setup()
    c = brain.intent("V1", last="你好呀", named=True, group=False, at=NOON, seed="1")
    assert "phrase:初见" in c.tags and "别做简历式自我介绍" in c.text
    brain.after(c, NOON)
    assert not Phrasebook(env.rt)._taken("初见", "V2", NOON)[1]  # 不算占用，8 位都能收到


async def test_first_meeting_sends_at_most_two_bubbles(first_meeting):
    env, _ = setup()
    dm = ChatHub(env.rt).room(dm_room("V1"))
    env.fake._default = lambda model, messages: "诶？\n\n老板好\n\n找我啥事\n\n我刚醒\n\n还困"
    msg, speakers = dm.post("你好")
    said = await dm.replies(msg["id"], speakers)
    rows = [r for r in env.rt.repo.chat_messages(dm_room("V1")) if r["author"] == "V1"]
    assert said and 1 <= len(rows) <= 2
