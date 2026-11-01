"""招牌特色（2026-10-31 用户点名：温柔带点小腹黑、尾音喵、念叨大白饭、豪横娇蛮）：
写进说话习惯段第一行、不会被字数挤掉；句尾小尾巴发出前按概率补上；口癖漂移不动招牌词；
一条里一个表情都没有时偶尔补一个她自己的颜文字；歪楼的意图只在有人刚说话时才可能出现。"""

from __future__ import annotations

import random

from roundtable.core.config.schema import Signature
from roundtable.core.member.brain import Brain
from roundtable.core.member.drift import Drift
from roundtable.core.member.voice import voice_for

from .test_brain import NOON, setup
from .test_lingo import _lingo, card, world


def sign(env, member: str, **fields) -> None:
    personas = env.rt.config.personas
    sigs = {**personas.signatures, member: Signature(**fields)}
    env.rt.config = env.rt.config.model_copy(
        update={"personas": personas.model_copy(update={"signatures": sigs})}
    )


def test_the_tail_goes_after_the_last_han_character_before_marks_and_stickers():
    env, lingo = world()
    sign(env, "V2", tails=["喵"], tail_chance=1.0)
    out = lingo.signature_tail("V2", "好耶！", random.Random(0))
    assert out == "好耶喵！"
    out = lingo.signature_tail("V2", "我也要[表情:小黄脸·开心]", random.Random(0))
    assert out == "我也要喵[表情:小黄脸·开心]"
    assert lingo.signature_tail("V2", "好困喵", random.Random(0)) == "好困喵"  # 已经带了
    assert lingo.signature_tail("V2", "666", random.Random(0)) == "666"  # 没有汉字不补
    two = lingo.signature_tail("V2", "吃了喵\n还想吃喵", random.Random(0))
    assert two == "吃了\n还想吃喵"  # 一条只留一个


def test_the_tail_is_kept_in_about_its_share_of_messages_and_others_are_untouched():
    """10-31 第 3 次试跑：模型自己句句带「喵」；程序按比例收放，没中的去掉句尾的。"""
    env, lingo = world()
    sign(env, "V2", tails=["喵"], tail_chance=0.33, self_names=["本喵"], self_chance=1.0)
    with_tail = sum(
        "喵" in lingo.signature_tail("V2", "今天好困喵～", random.Random(i)) for i in range(400)
    )
    assert 90 < with_tail < 180
    out = lingo.signature_tail("V2", "本喵饿了喵", random.Random(5))
    assert out.startswith("本喵饿了")  # 招牌自称里的字不算尾巴
    assert lingo.signature_tail("V3", "今天好困", random.Random(0)) == "今天好困"


def test_her_signature_self_name_is_used_only_now_and_then():
    env, lingo = world()
    sign(env, "V2", self_names=["本小姐"], self_chance=0.25)
    outs = [lingo.signature_tail("V2", "本小姐也有空。", random.Random(i)) for i in range(400)]
    kept = sum(o == "本小姐也有空。" for o in outs)
    assert 60 < kept < 150
    assert all(o in ("本小姐也有空。", "我也有空。") for o in outs)


def test_finish_adds_the_tail_even_without_a_card_but_not_when_lingo_is_off():
    env, lingo = world()
    sign(env, "V9", tails=["喵"], tail_chance=1.0)  # V9 没有人物卡
    assert lingo.finish("V9", "我先睡了", rng=random.Random(0)) == ["我先睡了喵"]
    _lingo(env, enabled=False)
    assert lingo.finish("V9", "我先睡了", rng=random.Random(0)) == ["我先睡了"]


def test_the_signature_heads_her_voice_and_is_never_squeezed_out():
    env, _ = world()
    card(env, "V2", style={"语气词": ["嗯", "呢"], "口头禅": ["我想想看"]})
    line = "温柔认真，偶尔有一点小腹黑"
    sign(env, "V2", line=line)
    voice = voice_for(env.rt, "V2", "life")
    assert voice.splitlines()[0] == f"招牌（让人一眼认出是你）：{line}"
    cfg = env.rt.config.roundtable
    tiny = cfg.chat.engine.voice.model_copy(update={"budget_chars": 10})
    engine = cfg.chat.engine.model_copy(update={"voice": tiny})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )
    assert line in voice_for(env.rt, "V2", "life")
    sign(env, "V9", line="尾音带喵")  # 没有档案也带上招牌
    assert "尾音带喵" in voice_for(env.rt, "V9", "life")
    assert "招牌" not in voice_for(env.rt, "V3", "life")


def test_drift_never_rests_or_counts_her_signature_words():
    env, lingo = world()
    sign(env, "V2", tails=["喵"], words=["本喵"])
    drift = Drift(env.rt)
    drift.resting = lambda member, at: {"本喵来啦", "说完了"}
    kept = drift.catchphrases("V2", ["本喵来啦", "说完了"], NOON)
    assert "本喵来啦" in kept and "说完了" not in kept
    assert drift.signature_words("V2") == ("喵", "本喵")
    sign(env, "V3", self_names=["本小姐"])
    assert drift.signature_words("V3") == ("本小姐",)


def test_a_kaomoji_is_sometimes_added_when_a_message_has_no_face_at_all():
    env, lingo = world(kaomoji_sprinkle=1.0, sprinkle_vibes=list(_all_vibes()))
    faces = lingo.habits("V2").kaomoji
    out = lingo._sprinkle("今天去跑步了", lingo.habits("V2"), random.Random(0))
    assert out.startswith("今天去跑步了") and out[len("今天去跑步了") :] in faces
    h = lingo.habits("V2")
    assert lingo._sprinkle("你吃了吗？", h, random.Random(0)) == "你吃了吗？"
    assert lingo._sprinkle("好耶[表情:小黄脸·开心]", h, random.Random(0)).count("[") == 1
    face = faces[0]
    assert lingo._sprinkle(f"今天去跑步了{face}", h, random.Random(0)) == f"今天去跑步了{face}"
    _, plain = world(kaomoji_sprinkle=0.0)
    assert plain._sprinkle("今天去跑步了", h, random.Random(0)) == "今天去跑步了"  # 默认关


def test_wandering_off_topic_needs_someone_to_have_just_said_something():
    env, brain = setup((3, 5, 3, 3, 3))
    rules = env.rt.config.roundtable.chat.engine.brain
    assert rules.intents["tangent"].needs == "last"
    alone = {
        brain.intent("V1", last="", named=True, group=False, at=NOON, seed=str(i)).intent
        for i in range(60)
    }
    assert "tangent" not in alone
    talked = {
        Brain(env.rt)
        .intent("V1", last="我昨天去看了海", named=True, group=False, at=NOON, seed=str(i))
        .intent
        for i in range(120)
    }
    assert "tangent" in talked


# --- 第 3 次真模型试跑后（10-31）---------------------------------------------------


def test_orphan_particles_and_lone_characters_are_tidied():
    from roundtable.core.member.lingo import _tidy

    assert (
        _tidy("想把今年的账本理清楚，呢。天台上吹了会儿风")
        == "想把今年的账本理清楚。天台上吹了会儿风"
    )
    assert _tidy("让……") == ""
    assert _tidy("嗯……") == "嗯……"
    assert _tidy("好吧，那就这样呢") == "好吧，那就这样呢"


def test_no_cheerful_face_is_added_when_she_cannot_remember_or_trails_off():
    env, lingo = world(kaomoji_sprinkle=1.0, sprinkle_vibes=list(_all_vibes()))
    h = lingo.habits("V2")
    assert (
        lingo._sprinkle("诶，这个我真想不起来了", h, random.Random(0)) == "诶，这个我真想不起来了"
    )
    assert lingo._sprinkle("今天去跑步了……", h, random.Random(0)) == "今天去跑步了……"
    env, lingo = world(kaomoji_sprinkle=1.0, sprinkle_vibes=[])
    assert lingo._sprinkle("今天去跑步了", lingo.habits("V2"), random.Random(0)) == "今天去跑步了"


def _all_vibes():
    from roundtable.core.config.loader import load_config

    return load_config().roundtable.chat.engine.lingo.kaomoji.keys()


def test_her_own_recent_sticker_is_not_repeated_after_a_sentence():
    from roundtable.core.chat_actions import drop_own_repeats

    mine = {"[表情:小黄脸·思考]"}
    assert drop_own_repeats("叫什么呀 [表情:小黄脸·思考]", mine) == "叫什么呀"
    assert (
        drop_own_repeats("[表情:小黄脸·思考]", mine) == "[表情:小黄脸·思考]"
    )  # 只发表情包的交给换一个
    assert drop_own_repeats("叫什么呀 [表情:小黄脸·开心]", mine) == "叫什么呀 [表情:小黄脸·开心]"
