"""大脑分区：驱力随时间涨、被事满足；目标会进步、卡住、过坎；空闲时自己挑去处；
说话前综合出意图卡（群里不显著就不接、私聊不沉默、没人发表看法时不会「不同意」）；
只写自己的状态、排日程先写内存；工作模式不挂载；同样的种子结果一样。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.member import WorkModeError
from roundtable.core.member.brain import KEY, Brain
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.member.mode import Mode
from roundtable.core.member.profile import ProfileStore

from ..orchestrator.conftest import Env

NOON = datetime(2026, 11, 2, 12, tzinfo=UTC)
TRAITS = ("外向", "较真", "好奇", "温和", "幽默")


def setup(scores=(3, 3, 3, 3, 3), likes=("弹吉他", "下围棋")) -> tuple[Env, Brain]:
    env = Env()
    env.rt.budget.now = lambda: NOON
    traits = dict(zip(TRAITS, scores, strict=True))
    ProfileStore(env.rt).change(
        "V1", {"personality": {"五项": traits}, "likes": {"最爱": list(likes)}}, author="user"
    )
    return env, Brain(env.rt)


def test_drives_grow_with_time_faster_for_outgoing_and_drop_when_satisfied():
    _, quiet = setup((1, 3, 3, 3, 3))
    _, loud = setup((5, 3, 3, 3, 3))
    for b in (quiet, loud):
        b.snapshot("V1", NOON)
        b.satisfy("V1", "spoke", NOON)  # 先落一次库
    later = NOON + timedelta(hours=5)
    q = quiet.snapshot("V1", later)["drives"]["social"]
    lo = loud.snapshot("V1", later)["drives"]["social"]
    assert lo > q
    loud.satisfy("V1", "spoke", later)
    assert loud.snapshot("V1", later)["drives"]["social"] < lo


def test_bus_spoke_and_gathered_lower_social_drive():
    env, _ = setup()
    bus = EventBus(env.rt)
    brain = Brain(env.rt, bus)
    brain.satisfy("V1", "explored", NOON)
    at = NOON + timedelta(hours=8)
    before = brain.snapshot("V1", at)["drives"]["social"]
    bus.publish(Event("life.event", at, "V1", {"kind": "gather"}))
    bus.publish(Event("chat.message", at, "V1", {}))
    assert brain.snapshot("V1", at)["drives"]["social"] < before


def test_goals_seed_from_likes_progress_and_leave_news_at_milestones():
    _, brain = setup()
    slot = NOON.replace(hour=19)
    seen_goal = False
    for day in range(40):
        at = slot + timedelta(days=day)
        brain.satisfy("V1", "explored", at)  # 不闷，多半去练在做的事
        place, act = brain.choose_activity("V1", at, "gym", "exercise")
        seen_goal |= act.startswith("琢磨「")
    snap = brain.snapshot("V1", slot + timedelta(days=40))
    assert {g["name"] for g in snap["goals"]} == {"和弹吉他有关的小计划", "和下围棋有关的小计划"}
    assert seen_goal and any(g["level"] > 0 for g in snap["goals"])
    assert snap["news"]  # 过坎或卡住都会留下没讲过的新鲜事


def test_only_free_slots_are_chosen_and_buffer_waits_for_flush():
    env, _ = setup()
    brain = Brain(env.rt, buffer={})
    assert brain.choose_activity("V1", NOON, "office", "idle") == ("office", "idle")
    assert brain.choose_activity("V1", NOON, "dorm", "sleep") == ("dorm", "sleep")
    brain.choose_activity("V1", NOON.replace(hour=20), "gym", "exercise")
    assert not env.rt.repo.setting(KEY + "V1")  # 只在内存里
    assert brain.flush() == 1 and env.rt.repo.setting(KEY + "V1")


def test_free_choice_off_keeps_the_timetable():
    env, brain = setup()
    rules = env.rt.config.roundtable.chat.engine.brain.model_copy(update={"free_choice": False})
    engine = env.rt.config.roundtable.chat.engine.model_copy(update={"brain": rules})
    chat = env.rt.config.roundtable.chat.model_copy(update={"engine": engine})
    rt_cfg = env.rt.config.roundtable.model_copy(update={"chat": chat})
    env.rt.config = env.rt.config.model_copy(update={"roundtable": rt_cfg})
    assert brain.choose_activity("V1", NOON.replace(hour=20), "gym", "exercise") == (
        "gym",
        "exercise",
    )


def test_group_stays_silent_when_nothing_salient_but_dm_always_answers():
    env, brain = setup((1, 3, 3, 3, 1))
    brain.satisfy("V1", "spoke", NOON)
    brain.satisfy("V1", "gathered", NOON)
    card = brain.intent("V1", last="今天天气一般", speaker="V2", group=True, at=NOON)
    assert card is not None and card.silent
    dm = brain.intent("V1", last="今天天气一般", named=True, group=False, at=NOON)
    assert dm is not None and not dm.silent and dm.text.startswith("此刻：")


def test_user_message_in_group_is_noticed():
    _, brain = setup((1, 3, 3, 3, 1))
    brain.satisfy("V1", "spoke", NOON)
    card = brain.intent("V1", last="今天天气一般", speaker=None, group=True, at=NOON)
    assert card is not None and not card.silent


def test_disagree_only_when_someone_made_a_claim_and_card_fits_budget():
    env, brain = setup((3, 5, 3, 1, 3))
    limit = env.rt.config.roundtable.chat.engine.brain.card_chars
    plain = {
        brain.intent("V1", last="下雨了", named=True, group=False, at=NOON, seed=str(i)).intent
        for i in range(60)
    }
    assert "disagree" not in plain
    claims = [
        brain.intent(
            "V1", last="我觉得火锅就是最好吃的", named=True, group=False, at=NOON, seed=str(i)
        )
        for i in range(60)
    ]
    assert any(c.intent == "disagree" for c in claims)
    assert all(len(c.text) <= limit for c in claims)


def test_goal_seeds_read_the_growth_chapter_in_any_format():
    from roundtable.core.member.brain import seeds

    a = (
        "现在心里的一个小愿望：\n希望公寓里有一张大家愿意围坐的长桌。\n\n"
        "最可能随时间改变的两三处：\n第一，我会学着更直接地表达自己的需要，而不是先写进账本再说。\n"
        "第二，我会慢慢接受有些账不必算到完美。\n\n无论怎样都不会变的核心：\n第一，先问清楚。"
    )
    assert seeds(a)[:2] == ["公寓里有一张大家愿意围坐的长桌", "学着更直接地表达自己的需要"]
    b = (
        "**最可能随时间改变的 2～3 处**\n\n1. **学会求助**：从「谢谢，不用」变成「帮我一下」。"
        "\n\n**小愿望**：还清第一笔债，然后喝茶。"
    )
    assert seeds(b) == ["还清第一笔债", "学会求助"]
    assert seeds("") == []


def test_same_seed_same_card_and_after_marks_news_told():
    env, brain = setup()
    s = brain.snapshot("V1", NOON)
    assert s["news"] == []
    raw = env.rt.repo.setting(KEY + "V1")
    a = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed="x")
    env.rt.repo.set_setting(KEY + "V1", raw or "")
    b = brain.intent("V1", last="在干嘛", named=True, group=False, at=NOON, seed="x")
    assert a == b
    # 有一件没讲过的新鲜事，讲完就不再当新鲜事
    brain._save("V1", brain._load("V1"))
    st = brain._load("V1")
    brain._news(st, "弹吉他入门了", NOON, 5.0)
    brain._save("V1", st)
    card = brain.intent("V1", group=False, named=True, at=NOON, seed="news")
    if card.spotlight and card.spotlight.text == "弹吉他入门了":
        brain.after(card, NOON)
        assert all(n["text"] != "弹吉他入门了" for n in brain.snapshot("V1", NOON)["news"])


def test_work_mode_is_refused():
    _, brain = setup()
    with pytest.raises(WorkModeError):
        brain.intent("V1", last="x", at=NOON, mode=Mode.WORK)


def test_disabled_returns_none_and_timetable():
    env, brain = setup()
    rules = env.rt.config.roundtable.chat.engine.brain.model_copy(update={"enabled": False})
    engine = env.rt.config.roundtable.chat.engine.model_copy(update={"brain": rules})
    chat = env.rt.config.roundtable.chat.model_copy(update={"engine": engine})
    rt_cfg = env.rt.config.roundtable.model_copy(update={"chat": chat})
    env.rt.config = env.rt.config.model_copy(update={"roundtable": rt_cfg})
    assert brain.intent("V1", last="x", at=NOON) is None
    assert brain.choose_activity("V1", NOON.replace(hour=20), "gym", "exercise") == (
        "gym",
        "exercise",
    )


def test_renaming_members_does_not_change_choices():
    """中立：同样的档案，换个成员 id，结果只随 id 派生的随机数变，规则一样（不认人）。"""
    env, brain = setup()
    ProfileStore(env.rt).change(
        "V2",
        {
            "personality": {"五项": dict.fromkeys(TRAITS, 3)},
            "likes": {"最爱": ["弹吉他", "下围棋"]},
        },
        author="user",
    )
    a = brain.snapshot("V1", NOON)
    b = brain.snapshot("V2", NOON)
    assert a["drives"] == b["drives"]
