"""阵容组建：按档位优先顺序抽组员、统筹不兼任组员、轮换。多种子重复。"""

from __future__ import annotations

import random

import pytest

from roundtable.core.allocation import NotEnoughModels
from roundtable.core.config.schema import CoordinatorPick
from roundtable.core.providers import FakeProvider
from roundtable.core.routing import LineupBuilder

from .conftest import POOL, Env, app_config

SEEDS = range(100)


def builder(cfg=None, seed=0, **kw):
    cfg = cfg or app_config()
    available = Env(cfg, FakeProvider("c")).router.available_models()
    return cfg, LineupBuilder(cfg, available, random.Random(seed), **kw)


def tiers(cfg, ids):
    by = {m.id: m for m in cfg.models.models}
    return [by[i].tier for i in ids]


def test_simple_plan_one_budget_no_coordinator():
    for seed in SEEDS:
        cfg, b = builder(seed=seed)
        lineup = b.build(cfg.routing.plans["simple"])
        assert len(lineup.members) == 1 and lineup.coordinator is None
        assert tiers(cfg, lineup.members) == ["budget"]
        assert lineup.pipeline == ("answer", "reveal")


def test_medium_with_three_budget_models_keeps_coordinator_budget():
    """3 个便宜档模型时：2 个组员 + 1 个便宜档统筹（整桌便宜档）。"""
    for seed in SEEDS:
        cfg, b = builder(seed=seed)
        lineup = b.build(cfg.routing.plans["medium"])
        assert len(lineup.members) == 2
        assert tiers(cfg, [*lineup.members, lineup.coordinator]) == ["budget"] * 3
        assert lineup.coordinator not in lineup.members


def test_medium_with_four_budget_models_seats_three():
    pool = [*POOL, ("b4", "V7", "budget", ["math"], 0.2, 0.8)]
    for seed in SEEDS:
        cfg, b = builder(app_config(pool=pool), seed=seed)
        lineup = b.build(cfg.routing.plans["medium"])
        assert len(lineup.members) == 3 and tiers(cfg, [lineup.coordinator]) == ["budget"]


def test_medium_coordinator_falls_back_to_flagship():
    pool = [p for p in POOL if p[0] not in ("b3",)]  # 只剩 2 个便宜档
    for seed in SEEDS:
        cfg, b = builder(app_config(pool=pool), seed=seed)
        lineup = b.build(cfg.routing.plans["medium"])
        assert tiers(cfg, lineup.members) == ["budget", "budget"]
        assert tiers(cfg, [lineup.coordinator]) == ["flagship"]


def test_hard_plan_four_flagships_and_separate_coordinator():
    for seed in SEEDS:
        cfg, b = builder(seed=seed)
        lineup = b.build(cfg.routing.plans["hard"])
        assert len(lineup.members) == 4
        assert tiers(cfg, lineup.members) == ["flagship"] * 4
        assert lineup.coordinator not in lineup.members
        assert tiers(cfg, [lineup.coordinator]) == ["flagship"]


def test_hard_fills_min_from_budget_when_flagships_scarce():
    pool = [p for p in POOL if p[2] == "budget"] + [POOL[3]]  # 只有 1 个旗舰
    for seed in SEEDS:
        cfg, b = builder(app_config(pool=pool), seed=seed)
        lineup = b.build(cfg.routing.plans["hard"])
        assert sorted(tiers(cfg, lineup.members)) == ["budget", "flagship"]


def test_not_enough_models():
    pool = [POOL[0]]
    cfg, b = builder(app_config(pool=pool))
    with pytest.raises(NotEnoughModels):
        b.build(cfg.routing.plans["medium"])


def test_required_tags_filter_members():
    for seed in SEEDS:
        cfg, b = builder(seed=seed, required_tags=["vision"])
        lineup = b.build(cfg.routing.plans["simple"])
        assert lineup.members == ("b2",)


def test_required_tags_unavailable():
    cfg, b = builder(required_tags=["video_gen"])
    with pytest.raises(NotEnoughModels, match="video_gen"):
        b.build(cfg.routing.plans["simple"])


def test_task_tags_preferred_when_enough():
    for seed in SEEDS:
        cfg, b = builder(seed=seed, task_type="math")
        lineup = b.build(cfg.routing.plans["simple"])
        assert lineup.members[0] in ("b1", "b2")  # 带 math 标签的便宜档


def test_task_tags_ignored_when_disabled():
    seen = set()
    for seed in SEEDS:
        cfg, b = builder(app_config(prefer_task_tags=False), seed=seed, task_type="math")
        seen.add(b.build(cfg.routing.plans["simple"]).members[0])
    assert seen == {"b1", "b2", "b3"}


def test_distinct_vendors_at_table():
    for seed in SEEDS:
        cfg, b = builder(seed=seed)
        lineup = b.build(cfg.routing.plans["hard"])
        by = {m.id: m for m in cfg.models.models}
        vendors = [by[i].vendor for i in [*lineup.members, lineup.coordinator]]
        assert len(set(vendors)) == len(vendors)


def test_rotation_avoids_recent_coordinators():
    for seed in SEEDS:
        cfg, b = builder(seed=seed, recent_coordinators=["b2", "b1"])
        lineup = b.build(cfg.routing.plans["medium"])
        assert lineup.coordinator == "b3"  # 唯一没当过统筹的便宜档


def test_rotation_most_recent_goes_last():
    cfg, b = builder(recent_coordinators=["b3", "b1", "b2"])  # b3 最近
    groups = b.coordinator_candidates(CoordinatorPick(tiers=["budget"]))
    assert [m.id for m in groups[0]][-1] == "b3"


def test_fixed_coordinator():
    rt = app_config().roundtable.model_copy(
        update={
            "coordinator": app_config().roundtable.coordinator.model_copy(
                update={"strategy": "fixed", "fixed_id": "f5"}
            )
        }
    )
    cfg = app_config().model_copy(update={"roundtable": rt})
    for seed in SEEDS:
        _, b = builder(cfg, seed=seed)
        assert b.build(cfg.routing.plans["medium"]).coordinator == "f5"


def test_manual_lineup():
    cfg, b = builder()
    pick = cfg.routing.manual.coordinator
    solo = b.build_manual(["f1"], None, pick)
    assert (
        solo.members == ("f1",)
        and solo.coordinator is None
        and solo.pipeline == ("answer", "reveal")
    )
    table = b.build_manual(["f1", "b3"], "f2", pick)
    assert table.members == ("f1", "b3") and table.coordinator == "f2"
    assert table.pipeline == tuple(cfg.roundtable.pipeline)
    auto = b.build_manual(["f1", "b3"], None, pick)
    assert auto.coordinator not in ("f1", "b3")
