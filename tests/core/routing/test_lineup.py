"""阵容组建：所选范围内全部可用模型上桌，统筹从中选出且不作答；轮换；缺席。多种子重复。"""

from __future__ import annotations

import random
from collections import Counter

import pytest

from roundtable.core.allocation import NotEnoughModels
from roundtable.core.providers import FakeProvider
from roundtable.core.routing import LineupBuilder

from .conftest import Env, app_config

SEEDS = range(100)


def builder(cfg=None, seed=0, **kw):
    cfg = cfg or app_config()
    available = Env(cfg, FakeProvider("c")).router.available_models()
    return cfg, LineupBuilder(cfg, available, random.Random(seed), **kw)


def ids_of_tier(cfg, tier):
    return {m.id for m in cfg.models.enabled if m.tier == tier}


@pytest.mark.parametrize("plan, tier", [("budget", "budget"), ("flagship", "flagship")])
def test_whole_tier_at_table(plan, tier):
    for seed in SEEDS:
        cfg, b = builder(seed=seed)
        lineup = b.build(cfg.routing.plans[plan])
        seated = {*lineup.members, lineup.coordinator}
        assert seated == ids_of_tier(cfg, tier)  # 全员上桌，一个都不少
        assert lineup.coordinator not in lineup.members  # 统筹不作答
        assert len(lineup.members) == len(seated) - 1
        assert lineup.absent == ()
        assert lineup.pipeline == tuple(cfg.roundtable.pipeline)


def test_coordinator_uniform_within_tier():
    cfg = app_config()
    counts = Counter()
    for seed in range(3000):
        _, b = builder(cfg, seed=seed)
        counts[b.build(cfg.routing.plans["budget"]).coordinator] += 1
    assert set(counts) == ids_of_tier(cfg, "budget")
    for c in counts.values():
        assert 850 < c < 1150, counts


def test_models_without_channels_are_absent():
    cfg = app_config()
    env = Env(cfg, FakeProvider("c"))
    available = [m for m in env.router.available_models() if m.id != "f5"]
    lineup = LineupBuilder(cfg, available, random.Random(1)).build(cfg.routing.plans["flagship"])
    assert lineup.absent == ("f5",)
    assert "f5" not in {*lineup.members, lineup.coordinator}


def test_disabled_models_are_not_absent():
    cfg = app_config(disabled=("f5",))
    _, b = builder(cfg)
    lineup = b.build(cfg.routing.plans["flagship"])
    assert lineup.absent == () and "f5" not in lineup.members


def test_not_enough_models_names_absentees():
    cfg = app_config()
    env = Env(cfg, FakeProvider("c"))
    available = [m for m in env.router.available_models() if m.id != "b3"]
    b = LineupBuilder(cfg, available, random.Random(1))
    with pytest.raises(NotEnoughModels, match="至少需要 3 个") as info:
        b.build(cfg.routing.plans["budget"])
    assert "b3" in str(info.value)


def test_more_members_than_seats_rejected():
    cfg = app_config()
    rt = cfg.roundtable.model_copy(update={"seats": 3})
    cfg = cfg.model_copy(update={"roundtable": rt})
    _, b = builder(cfg)
    with pytest.raises(NotEnoughModels, match="超过座位数"):
        b.build(cfg.routing.plans["flagship"])  # 5 个旗舰 = 4 个组员 > 3


def test_rotation_avoids_recent_coordinators():
    for seed in SEEDS:
        cfg, b = builder(seed=seed, recent_coordinators=["b1", "b2"])
        assert b.build(cfg.routing.plans["budget"]).coordinator == "b3"


def test_rotation_most_recent_goes_last():
    for seed in SEEDS:
        cfg, b = builder(seed=seed, recent_coordinators=["b1", "b3", "b2"])
        # 都当过：最久之前当过的（b2）优先
        assert b.build(cfg.routing.plans["budget"]).coordinator == "b2"


def test_fixed_coordinator():
    cfg = app_config()
    rule = cfg.roundtable.coordinator.model_copy(update={"strategy": "fixed", "fixed_id": "b2"})
    rt = cfg.roundtable.model_copy(update={"coordinator": rule})
    cfg = cfg.model_copy(update={"roundtable": rt})
    for seed in SEEDS:
        _, b = builder(cfg, seed=seed)
        assert b.build(cfg.routing.plans["budget"]).coordinator == "b2"
        # 固定的统筹不在这个档位时，按轮换规则在场内选
        assert b.build(cfg.routing.plans["flagship"]).coordinator in ids_of_tier(cfg, "flagship")


def test_custom_lineup():
    cfg, b = builder()
    lineup = b.build_custom(["b1", "f1", "f2"], "f1")
    assert lineup.members == ("b1", "f2") and lineup.coordinator == "f1"
    for seed in SEEDS:
        _, b = builder(seed=seed)
        auto = b.build_custom(["b1", "f1", "f2", "f3"], None)
        assert {*auto.members, auto.coordinator} == {"b1", "f1", "f2", "f3"}
        assert len(auto.members) == 3
    with pytest.raises(NotEnoughModels):
        b.build_custom(["b1", "f1"], None)  # 2 个组员 + 1 个统筹 至少 3 个


# --- 个人作答（R13）---------------------------------------------------------------


@pytest.mark.parametrize("plan, cheapest_id", [("budget", "b1"), ("flagship", "f5")])
def test_solo_picks_cheapest_in_tier_without_coordinator(plan, cheapest_id):
    for seed in SEEDS:
        cfg, b = builder(seed=seed)
        lineup = b.build(cfg.routing.plans[plan], "solo")
        assert lineup.members == (cheapest_id,) and lineup.coordinator is None
        assert lineup.pipeline == tuple(cfg.roundtable.solo_pipeline)


def test_solo_respects_required_tags_then_relaxes():
    cfg, b = builder()
    assert b.build(cfg.routing.plans["budget"], "solo", ("vision",)).members == ("b2",)
    # 没有任何模型满足标签时放宽，仍选出一位而不是报错
    assert b.build(cfg.routing.plans["budget"], "solo", ("nonexistent",)).members == ("b1",)


def test_solo_custom_needs_exactly_one_model():
    cfg, b = builder()
    assert b.build_custom(["b3"], None, "solo").members == ("b3",)
    with pytest.raises(NotEnoughModels):
        b.build_custom(["b1", "b2"], None, "solo")


# --- 办公室模式（R17 O2）-----------------------------------------------------------


@pytest.mark.parametrize("plan", ["budget", "flagship"])
def test_office_manager_from_config_and_staff_from_tier(plan):
    managers = Counter()
    for seed in SEEDS:
        cfg, b = builder(seed=seed)
        lineup = b.build(cfg.routing.plans[plan], "office")
        assert lineup.coordinator in {"f1", "f2"}  # 经理只来自 office_role: manager
        managers[lineup.coordinator] += 1
        staff = ids_of_tier(cfg, plan) - {"f1", "f2"}
        assert set(lineup.members) == staff  # 经理不兼任员工
        assert lineup.pipeline == tuple(cfg.roundtable.office_pipeline)
    assert set(managers) == {"f1", "f2"}  # 两位经理都会主办


def test_office_without_manager_is_rejected():
    cfg = app_config()
    cfg = cfg.model_copy(
        update={
            "models": cfg.models.model_copy(
                update={
                    "models": [
                        m.model_copy(update={"office_role": "staff"}) for m in cfg.models.models
                    ]
                }
            )
        }
    )
    _, b = builder(cfg)
    with pytest.raises(NotEnoughModels, match="经理"):
        b.build(cfg.routing.plans["budget"], "office")


def test_office_custom_uses_picked_staff_and_config_managers():
    cfg, b = builder()
    lineup = b.build_custom(["b2", "b3"], None, "office")
    assert set(lineup.members) == {"b2", "b3"} and lineup.coordinator in {"f1", "f2"}


def test_media_models_are_never_listed_as_absent():
    cfg = app_config(with_media=True)
    _, b = builder(cfg)
    for plan in ("budget", "flagship"):
        lineup = b.build(cfg.routing.plans[plan])
        assert lineup.absent == (), plan  # 媒体模型（seat: false）不上桌，不算缺席
