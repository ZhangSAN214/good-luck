"""网络梗（懂梗、玩梗）：梗库与保质期、经典梗不下架、老梗烂梗删掉、谁懂只看性格、
上句 → 接法（懂的直接接、不懂的问）、偶尔提一句最近的梗、同一个梗隔几天才再用。"""

from __future__ import annotations

import random
from datetime import timedelta

import pytest

from roundtable.core.config.schema import ClassicMeme
from roundtable.core.member.brain import Brain
from roundtable.core.member.memes import MemeBook, MemeSense, strip_stale

from .test_brain import NOON, setup
from .test_lingo import card, world

EARLIER = NOON - timedelta(days=5)  # 火了几天：懂的人到满了
KANG = {
    "phrase": "康神开播",
    "meaning": "康神终于开直播了",
    "cue": "康神开播了",
    "reply": "真的假的？！",
}


def line(sense, *, chosen, hurt, at, last=""):
    return sense.line("V1", last=last, chosen=chosen, hurt=hurt, at=at, rng=random.Random(0))


def memes(env, **update) -> None:
    cfg = env.rt.config.roundtable
    rules = cfg.chat.memes.model_copy(update=update)
    chat = cfg.chat.model_copy(update={"memes": rules})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def test_stale_memes_are_cut_out_clause_by_clause():
    stale = ["yyds", "绝绝子"]
    assert strip_stale("这家店绝绝子，明天还去", stale) == "明天还去"
    assert strip_stale("YYDS！", stale) == ""
    assert strip_stale("好吃\n真的yyds", stale) == "好吃"
    assert strip_stale("今天去跑步了", stale) == "今天去跑步了"
    assert strip_stale("随便", []) == "随便"


def test_finish_drops_stale_memes_before_speaking():
    env, lingo = world()
    memes(env, stale=["绝绝子"])
    assert lingo.finish("V2", "绝绝子") == [""]
    out = lingo.finish("V2", "这个绝绝子，我明天再去一次")[0]
    assert "绝绝子" not in out and "明天再去" in out


def test_new_memes_expire_classics_stay_and_removed_ones_never_come_back():
    env, _ = setup()
    memes(env, classics=[ClassicMeme(phrase="虾爹", aliases=["NiKo"])], fresh_days=14)
    book = MemeBook(env.rt)
    book.add({**KANG, "classic": False}, NOON - timedelta(days=20))
    book.add({"phrase": "新梗", "meaning": "刚火的", "classic": False}, NOON)
    phrases = [m["phrase"] for m in book.all(NOON)]
    assert phrases == ["虾爹", "新梗"]  # 经典梗在前；20 天没再上榜的下架
    assert book.all(NOON)[0]["classic"] is True
    assert book.remove("新梗") and not book.remove("没有的梗")
    assert [m["phrase"] for m in book.all(NOON)] == ["虾爹"]
    assert book.remove("虾爹")  # 配置里的经典梗也能删（记进删除名单）
    assert book.all(NOON) == []
    book.add({"phrase": "虾爹", "meaning": "NiKo 的外号"}, NOON)  # 你再加回来就又有了
    assert book.all(NOON)[0]["meaning"] == "NiKo 的外号"


def test_stale_phrases_cannot_be_added():
    env, _ = setup()
    memes(env, stale=["yyds"])
    with pytest.raises(ValueError):
        MemeBook(env.rt).add({"phrase": "YYDS", "classic": False}, NOON)


def test_who_knows_a_meme_depends_only_on_personality_and_ripens_over_days():
    env, _ = setup()
    memes(env, know_base=0.5, know_traits={"幽默": 0.1, "外向": 0.1}, know_min=0.0, learn_days=4)
    card(env, "V2", (5, 3, 3, 3, 5))  # 外向、幽默都高
    card(env, "V3", (1, 3, 3, 3, 1))
    book = MemeBook(env.rt)
    meme = {"phrase": "x", "first_seen": (NOON - timedelta(days=10)).isoformat()}
    assert book.chance("V2", meme, NOON) == pytest.approx(0.9)
    assert book.chance("V3", meme, NOON) == pytest.approx(0.1)
    young = {"phrase": "x", "first_seen": NOON.isoformat()}
    assert book.chance("V2", young, NOON) == pytest.approx(0.45)  # 刚出来时懂的人少
    card(env, "V4", (5, 3, 3, 3, 5))  # 同样的性格，同样的概率（不看是谁）
    assert book.chance("V4", meme, NOON) == book.chance("V2", meme, NOON)
    many = [{"phrase": f"梗{i}", "first_seen": meme["first_seen"]} for i in range(300)]
    assert (
        sum(book.knows("V2", m, NOON) for m in many)
        > sum(book.knows("V3", m, NOON) for m in many) + 150
    )


def test_hearing_the_cue_she_knows_she_answers_with_the_reply_without_a_model_call():
    env, brain = setup()
    memes(env, know_min=1.0, know_max=1.0, quick_chance=1.0)
    MemeBook(env.rt).add(KANG, EARLIER)
    card_ = brain.intent("V1", last="康神开播了", named=True, group=False, at=NOON, seed="1")
    assert card_.quick == "真的假的？！"
    assert "对方在玩梗「康神开播」" in card_.text and "meme:reply:康神开播" in card_.tags
    brain.after(card_, NOON)
    assert MemeBook(env.rt).used("V1")  # 接过就算用过


def test_a_meme_she_does_not_know_makes_her_ask():
    env, brain = setup()
    memes(env, know_min=0.0, know_max=0.0, ask_chance=1.0)
    MemeBook(env.rt).add(KANG, EARLIER)
    card_ = brain.intent("V1", last="康神开播了！", named=True, group=False, at=NOON, seed="1")
    assert "没听过是啥梗" in card_.text and card_.quick != "真的假的？！"


def test_now_and_then_she_is_reminded_of_a_fresh_meme_but_not_the_same_one_again_soon():
    env, _ = setup()
    memes(env, know_min=1.0, know_max=1.0, use_chance=1.0, reuse_days=3)
    MemeBook(env.rt).add(KANG, EARLIER)
    sense = MemeSense(env.rt)
    got = line(sense, chosen="joke", hurt=False, at=NOON)
    assert got is not None and got[0] == "meme:use:康神开播" and "别解释" in got[1]
    assert line(sense, chosen="comfort", hurt=False, at=NOON) is None
    assert line(sense, chosen="joke", hurt=True, at=NOON) is None
    sense.after("V1", (got[0],), NOON)
    later = NOON + timedelta(days=1)
    assert line(sense, chosen="joke", hurt=False, at=later) is None
    again = NOON + timedelta(days=4)
    assert line(sense, chosen="joke", hurt=False, at=again)


def test_memes_without_a_meaning_are_not_suggested_and_switching_off_silences_everything():
    env, _ = setup()
    memes(env, know_min=1.0, know_max=1.0, use_chance=1.0)
    MemeBook(env.rt).add({"phrase": "神秘梗"}, NOON)
    sense = MemeSense(env.rt)
    assert line(sense, chosen="joke", hurt=False, at=NOON) is None
    MemeBook(env.rt).add(KANG, EARLIER)
    memes(env, enabled=False)
    assert (
        sense.line(
            "V1", last="康神开播了", chosen="respond", hurt=False, at=NOON, rng=random.Random(0)
        )
        is None
    )


def test_memes_do_not_shift_the_other_brain_zones():
    """梗用自己的随机数：同一张卡，开不开梗，别的部分都一样。"""
    env, brain = setup()
    on = brain.intent("V1", last="今天好热", named=True, group=False, at=NOON, seed="7")
    memes(env, enabled=False)
    off = Brain(env.rt).intent("V1", last="今天好热", named=True, group=False, at=NOON, seed="7")
    assert on.intent == off.intent and on.text == off.text


async def test_in_a_real_dm_she_answers_the_cue_with_the_reply_and_no_model_call():
    """端到端：私聊里你说「康神开播了」，她懂又抽中直接接 → 发「真的假的？！」，不调模型。"""
    from roundtable.core.chat import ChatHub, dm_room

    env, _ = setup()
    memes(env, know_min=1.0, know_max=1.0, quick_chance=1.0)
    MemeBook(env.rt).add(KANG, EARLIER)
    dm = ChatHub(env.rt).room(dm_room("V1"))
    before = len(env.fake.calls)
    msg, speakers = dm.post("康神开播了")
    said = await dm.replies(msg["id"], speakers)
    assert len(said) == 1 and said[0]["text"].startswith("真的假的？")  # 标点按她的打字习惯
    assert said[0]["cost_usd"] == 0 and len(env.fake.calls) == before
