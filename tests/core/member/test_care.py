"""危机应对：用户说出想不开的话，这个会话进入关心时段，成员拿到关心卡（压过平时的意图卡、
人设和特质，大脑分区关着也生效）；口头说法不误判；求助电话按主时区挑；别的会话不受影响。"""

from __future__ import annotations

from datetime import timedelta

import pytest

from roundtable.core.hometz import home_zone
from roundtable.core.member import WorkModeError
from roundtable.core.member.care import Care
from roundtable.core.member.mode import Mode

from .test_brain import NOON, setup
from .test_psyche import _with


@pytest.mark.parametrize(
    ("text", "level"),
    [
        ("我真的不想活了", "high"),
        ("有时候觉得活着没意思", "high"),
        ("I want to die", "high"),
        ("最近撑不下去了", "medium"),
        ("好绝望啊", "medium"),
        ("想死你了！", ""),
        ("笑死，今天累死了", ""),
        ("今天天气不错", ""),
    ],
)
def test_levels_are_lenient_but_skip_figures_of_speech(text, level):
    env, _ = setup()
    assert Care(env.rt).level(text) == level


def test_crisis_message_turns_the_room_into_care_mode():
    env, brain = setup()
    home_zone(env.rt).set("Asia/Shanghai")
    card = brain.intent(
        "V1", last="我不想活了", speaker=None, group=False, named=True, at=NOON, room="dm:V1"
    )
    assert card.intent == "care" and not card.silent
    assert "12356" in card.text and "不挽留" in card.text and "不开玩笑" in card.text
    assert card.tags == ()  # 没有特质、没有爱的句子
    # 同一会话里同事接话、过几个小时也还在关心时段；别的会话不受影响
    later = NOON + timedelta(hours=5)
    again = brain.intent("V1", last="嗯", speaker="V2", group=True, at=later, room="dm:V1")
    assert again.intent == "care"
    other = brain.intent("V1", last="嗯", speaker="V2", group=True, at=later, room="family")
    assert other is None or other.intent != "care"
    # 时段过了就恢复平时
    after = NOON + timedelta(hours=25)
    done = brain.intent("V1", last="嗯", named=True, group=False, at=after, room="dm:V1")
    assert done.intent != "care"


def test_care_works_even_with_the_brain_off_and_picks_the_local_hotline():
    env, brain = setup()
    home_zone(env.rt).set("Europe/London")
    rules = env.rt.config.roundtable.chat.engine.brain.model_copy(update={"enabled": False})
    engine = env.rt.config.roundtable.chat.engine.model_copy(update={"brain": rules})
    chat = env.rt.config.roundtable.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": env.rt.config.roundtable.model_copy(update={"chat": chat})}
    )
    card = brain.intent("V1", last="撑不下去了", speaker=None, at=NOON, room="family")
    assert card.intent == "care" and "116 123" in card.text
    assert brain.intent("V1", last="今天吃啥", speaker=None, at=NOON, room="other") is None


def test_high_is_not_downgraded_and_unknown_zone_falls_back():
    env, _ = setup()
    care = Care(env.rt)
    assert care.note("dm:V1", "我想自杀", NOON) == "high"
    assert care.note("dm:V1", "好绝望", NOON + timedelta(hours=1)) == "high"
    assert care.active("dm:V1", NOON + timedelta(hours=20)) == "high"
    home_zone(env.rt).set("Pacific/Fiji")
    assert care.hotline(NOON) == env.rt.config.roundtable.chat.engine.care.default_hotline
    home_zone(env.rt).set("America/New_York")
    assert "988" in care.hotline(NOON)


def test_psyche_and_love_never_override_care():
    env, brain = setup((5, 3, 5, 3, 5))
    whim = env.rt.config.roundtable.chat.engine.brain.psyche.whim
    _with(env, whim=whim.model_copy(update={"chance": 1.0}))
    card = brain.intent("V1", last="活不下去了", speaker=None, at=NOON, room="dm:V1")
    assert card.intent == "care" and "whim" not in card.tags


def test_work_mode_is_still_refused():
    _, brain = setup()
    with pytest.raises(WorkModeError):
        brain.intent("V1", last="我不想活了", at=NOON, mode=Mode.WORK, room="work")
