"""生活阶段与小意外：每天按随机数（成员 + 日期派生、可复现）排一段忙季 / 小感冒 / 迷上一样东西……
同时最多两位、结束后有冷却；阶段挪精力和心情、让私聊回得慢一点、在念头池里放一件可说的事
（开始那天是新鲜事，中间是「最近在……」，刚结束一天内是「刚……完」）；迷上的东西从档案的最爱里挑；
小病不博同情；关掉不动。"""

from __future__ import annotations

from datetime import datetime, timedelta

from roundtable.core.member.phase import PHASE_KEY, Phase
from roundtable.core.member.state import StatePlugin

from .test_brain import NOON, setup


def rules(env, **update) -> None:
    cfg = env.rt.config.roundtable
    engine = cfg.chat.engine
    ph = engine.phase.model_copy(update=update)
    chat = cfg.chat.model_copy(update={"engine": engine.model_copy(update={"phase": ph})})
    env.rt.config = env.rt.config.model_copy(
        update={"roundtable": cfg.model_copy(update={"chat": chat})}
    )


def only(env, name, **update):
    kinds = [k for k in env.rt.config.roundtable.chat.engine.phase.kinds if k.name == name]
    rules(env, kinds=kinds, **update)


def test_table_is_the_same_for_everyone_and_sick_days_do_not_guilt_trip():
    from roundtable.core.config import load_config

    ph = load_config().roundtable.chat.engine.phase
    names = {k.name for k in ph.kinds}
    assert {"忙季", "小感冒", "迷上", "收拾屋子"} <= names
    sick = next(k for k in ph.kinds if k.name == "小感冒")
    assert "别拿生病博同情" in sick.note and sick.energy < 0


def test_rolls_once_a_day_reproducibly_and_lasts_its_days(life_phase):
    env, _ = setup()
    only(env, "忙季", chance_per_day=1.0)
    phase = Phase(env.rt)
    row = phase.current("V1", NOON)
    assert row is not None and row["kind"] == "忙季"
    span = datetime.fromisoformat(row["end"]) - datetime.fromisoformat(row["start"])
    assert 4 <= span.days <= 10
    assert phase.current("V1", NOON + timedelta(hours=3)) == row  # 同一段
    env.rt.repo.set_setting(PHASE_KEY, "{}")
    assert Phase(env.rt).current("V1", NOON) == row  # 可复现


def test_at_most_two_at_once_and_cooldown_after(life_phase):
    env, _ = setup()
    only(env, "收拾屋子", chance_per_day=1.0, cooldown_days=5)
    phase = Phase(env.rt)
    got = [phase.current(m, NOON) for m in ("V1", "V2", "V3")]
    assert sum(1 for g in got if g) == 2  # 不会全员一起
    later = NOON + timedelta(days=3)  # 收拾屋子最多 2 天：已经结束，但还在冷却
    assert phase.current("V1", later) is None
    assert phase.current("V3", later) is not None  # 没排过的照常排
    assert phase.current("V1", NOON + timedelta(days=8))


def test_not_rolled_and_switched_off():
    env, _ = setup()
    only(env, "忙季", chance_per_day=0.0)
    assert Phase(env.rt).current("V1", NOON) is None
    rules(env, enabled=False)
    assert Phase(env.rt).thoughts("V1", NOON) == [] and Phase(env.rt).energy("V1", NOON) == 0


def test_thoughts_start_middle_and_just_after_end(life_phase):
    env, _ = setup(likes=("拼乐高",))
    only(env, "迷上", chance_per_day=1.0)
    phase = Phase(env.rt)
    first = phase.thoughts("V1", NOON)
    assert first == [("最近迷上了拼乐高", phase.rules.start_strength)]
    row = phase.current("V1", NOON)
    end = datetime.fromisoformat(row["end"])
    mid = phase.thoughts("V1", NOON + timedelta(days=2))
    assert mid and "拼乐高" in mid[0][0] and mid[0][1] == phase.rules.during_strength
    after = phase.thoughts("V1", end + timedelta(hours=2))
    assert after == [("对拼乐高的劲儿过去了一点", phase.rules.end_strength)]
    assert phase.thoughts("V1", end + timedelta(days=2)) == []


def test_phase_shifts_energy_and_makes_dm_replies_slower(life_phase):
    env, _ = setup()
    rules(env, enabled=False)
    before = StatePlugin(env.rt).read("V1", NOON).energy
    only(env, "小感冒", chance_per_day=1.0, enabled=True)
    after = StatePlugin(env.rt).read("V1", NOON).energy
    assert after < before
    assert Phase(env.rt).busy("V1", NOON) > 0
    listing = Phase(env.rt).listing()
    assert listing["V1"]["label"] == "小感冒"


def test_brain_card_can_mention_it(life_phase):
    env, brain = setup()
    only(env, "收拾屋子", chance_per_day=1.0, start_strength=5.0)
    texts = set()
    for seed in range(20):
        card = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed=str(seed))
        texts.add(card.text)
    assert any("收拾屋子" in t for t in texts)
