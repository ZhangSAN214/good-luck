"""去模板化：公共素材每位只分到一部分；意图卡的附加提示有条数上限、说出口后要冷却；
放人、挽留、责怪这几句不受限；新版提示词让感情用做法流露、分寸说明不说出口。"""

from __future__ import annotations

from roundtable.core.member import psyche
from roundtable.core.member.love import USER
from roundtable.core.prompts import PromptLibrary

from .test_brain import NOON, setup
from .test_love import bond
from .test_psyche import RULES, _with

POOL = [f"念头{i}" for i in range(20)]


def _brain(env, **update) -> None:
    cfg = env.rt.config.roundtable
    brain = cfg.chat.engine.brain.model_copy(update=update)
    engine = cfg.chat.engine.model_copy(update={"brain": brain})
    chat = cfg.chat.model_copy(update={"engine": engine})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def _tired(monkeypatch):
    import roundtable.core.member.state as state_mod

    reading = type("R", (), {"mood": -0.6, "energy": 0.1})()
    monkeypatch.setattr(state_mod.StatePlugin, "read", lambda self, m, at: reading)


def test_each_member_gets_a_different_slice_of_shared_material():
    a = psyche.personal(POOL, "V1", "whim", 0.5)
    b = psyche.personal(POOL, "V2", "whim", 0.5)
    assert len(a) == len(b) == 10 and a != b
    assert a == psyche.personal(POOL, "V1", "whim", 0.5)  # 可复现
    assert psyche.personal(POOL, "V1", "whim", 1.0) == POOL
    assert psyche.personal(["只有一条"], "V1", "whim", 0.1) == ["只有一条"]


def test_cards_carry_at_most_max_cues_extra_lines(monkeypatch):
    env, brain = setup()
    _tired(monkeypatch)
    confab = RULES.confab.model_copy(update={"chance": 1.0, "misattribute": 1.0})
    _with(env, extra_lines=5, confab=confab)
    _brain(env, max_cues=1, card_chars=400)
    card = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed="m")
    assert len(card.cues) == 1
    _brain(env, max_cues=0, card_chars=400)
    card = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed="m")
    assert card.cues == () and "其实是" not in card.text


def test_a_spoken_cue_cools_down_but_an_unspoken_one_does_not(monkeypatch):
    env, brain = setup()
    _tired(monkeypatch)
    confab = RULES.confab.model_copy(update={"chance": 0.0, "misattribute": 1.0})
    _with(env, extra_lines=5, confab=confab)
    first = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed="1")
    assert "misattr" in first.tags
    again = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed="2")
    assert "misattr" in again.tags  # 候选卡没说出口，不算用过
    brain.after(first, NOON)
    cooled = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed="3")
    assert "misattr" not in cooled.tags
    _brain(env, cooldown_cards=0)
    assert "misattr" in brain.intent("V1", last="在干嘛", group=False, at=NOON, seed="4").tags


def test_letting_the_user_go_is_never_dropped_by_the_limits():
    env, brain = setup()
    _brain(env, max_cues=0)
    from roundtable.core.member.love import Heart

    bond(Heart(env.rt), "V1", USER, care=0.8)
    card = brain.intent("V1", last="困了，我去睡了", group=False, at=NOON, room="dm:V1")
    assert "love:let_go" in card.tags and "别留" in card.text


def test_new_prompts_show_feelings_through_behaviour_and_allow_love_with_limits():
    lib = PromptLibrary()
    cfg = setup()[0].rt.config.roundtable.prompts
    for role in ("chat", "chat_dm"):
        text = lib.get(role, cfg[role]).system
        assert "不要说出口" in text and "只留一次" in text and "不装懂" in text
        assert "不制造依赖：不挽留" not in text
        assert "intent" in lib.get(role, cfg[role]).variables
    life = lib.get("life_chat", cfg["life_chat"])
    assert "intent" in life.variables and "不要说出口" in life.system
