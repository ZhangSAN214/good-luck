"""按方案组建阵容：组员（按档位优先顺序抽取）+ 统筹（不兼任组员）。只看档位与标签。"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from roundtable.core.allocation import NotEnoughModels, draw, eligible, prefer_tags
from roundtable.core.config import AppConfig, ModelSpec
from roundtable.core.config.schema import CoordinatorPick, MemberPick, PlanSpec


@dataclass(frozen=True)
class Lineup:
    members: tuple[str, ...]
    coordinator: str | None
    pipeline: tuple[str, ...]


class LineupBuilder:
    def __init__(
        self,
        config: AppConfig,
        available: Sequence[ModelSpec],
        rng: random.Random,
        *,
        required_tags: Iterable[str] = (),
        task_type: str | None = None,
        recent_coordinators: Sequence[str] = (),
    ) -> None:
        self.config = config
        self.available = list(available)
        self.rng = rng
        self.required_tags = tuple(required_tags)
        self.task_type = task_type
        self.recent_coordinators = list(recent_coordinators)
        self.distinct = config.routing.prefer_distinct_vendors

    # --- 组员 -------------------------------------------------------------------

    def pick_members(
        self, pick: MemberPick, *, exclude: Iterable[str] = (), avoid_vendors: Iterable[str] = ()
    ) -> list[ModelSpec]:
        """先从第一个档位抽到 max；不够 min 时依次用后面的档位补到 min。"""
        excluded = set(exclude)
        avoid = set(avoid_vendors)
        chosen: list[ModelSpec] = []
        for i, tier in enumerate(pick.tiers):
            target = pick.max if i == 0 else pick.min
            need = target - len(chosen)
            if need <= 0:
                break
            pool = eligible(
                self.available,
                tiers=[tier],
                required_tags=self.required_tags,
                exclude=excluded | {m.id for m in chosen},
            )
            if self.config.routing.prefer_task_tags and self.task_type:
                pool = prefer_tags(pool, [self.task_type], min(need, len(pool)))
            got = draw(
                pool,
                min(need, len(pool)),
                self.rng,
                distinct_vendors=self.distinct,
                avoid_vendors=avoid | {m.vendor for m in chosen},
            )
            chosen.extend(got)
        if len(chosen) < pick.min:
            tags = f"（需要标签 {list(self.required_tags)}）" if self.required_tags else ""
            raise NotEnoughModels(
                f"档位 {pick.tiers} 中可用模型不足 {pick.min} 个{tags}，只找到 {len(chosen)} 个"
            )
        return chosen

    # --- 统筹 -------------------------------------------------------------------

    def coordinator_candidates(
        self, pick: CoordinatorPick, exclude: Iterable[str] = ()
    ) -> list[list[ModelSpec]]:
        """按档位分组的统筹候选，每组内已按轮换规则排序（随机 + 最近用过的排后）。"""
        rule = self.config.roundtable.coordinator
        excluded = set(exclude)
        if rule.strategy == "fixed":
            fixed = [m for m in self.available if m.id == rule.fixed_id and m.id not in excluded]
            return [fixed] if fixed else []
        groups = []
        for tier in pick.tiers:
            pool = eligible(self.available, tiers=[tier], exclude=excluded)
            self.rng.shuffle(pool)
            if rule.strategy == "rotate":
                # recent_coordinators 按"最近的在前"排列：没用过的在前，越近用过的越靠后
                recent = self.recent_coordinators
                pool.sort(key=lambda m: len(recent) - recent.index(m.id) if m.id in recent else 0)
            groups.append(pool)
        return [g for g in groups if g]

    def pick_coordinator(self, pick: CoordinatorPick, members: Sequence[ModelSpec]) -> ModelSpec:
        member_ids = {m.id for m in members}
        groups = self.coordinator_candidates(pick, exclude=member_ids)
        if not groups:
            raise NotEnoughModels(f"档位 {pick.tiers} 中没有可担任统筹的空余模型")
        group = groups[0]
        if self.distinct:
            vendors = {m.vendor for m in members}
            different = [m for m in group if m.vendor not in vendors]
            group = different or group
        return group[0]

    # --- 方案 -------------------------------------------------------------------

    def build(self, plan: PlanSpec) -> Lineup:
        pipeline = tuple(plan.pipeline or self.config.roundtable.pipeline)
        if plan.coordinator is None:
            members = self.pick_members(plan.members)
            return Lineup(tuple(m.id for m in members), None, pipeline)

        # 遍历统筹候选，对每个候选抽组员，按优先级挑最好的组合：
        #   1. 组员中首选档位的人数（最多计到 min）——保证"旗舰圆桌"的组员先拿到旗舰；
        #   2. 统筹所在档位越靠前越好——保证"小圆桌"的统筹也留在便宜档；
        #   3. 首选档位的组员越多越好。
        first_tier = plan.members.tiers[0]
        best: tuple[tuple[int, int, int], Lineup] | None = None
        last_error: NotEnoughModels | None = None
        for rank, group in enumerate(self.coordinator_candidates(plan.coordinator)):
            for candidate in group:
                try:
                    members = self.pick_members(
                        plan.members, exclude=[candidate.id], avoid_vendors=[candidate.vendor]
                    )
                except NotEnoughModels as exc:
                    last_error = exc
                    continue
                in_first = sum(1 for m in members if m.tier == first_tier)
                score = (min(in_first, plan.members.min), -rank, in_first)
                if best is None or score > best[0]:
                    lineup = Lineup(tuple(m.id for m in members), candidate.id, pipeline)
                    best = (score, lineup)
        if best is None:
            raise last_error or NotEnoughModels("没有可担任统筹的模型")
        return best[1]

    def build_manual(
        self, member_ids: Sequence[str], coordinator_id: str | None, pick: CoordinatorPick
    ) -> Lineup:
        by_id = {m.id: m for m in self.available}
        members = [by_id[i] for i in member_ids]
        if len(members) == 1:
            solo = self.config.routing.plans[self.config.routing.difficulty_plans["simple"]]
            pipeline = tuple(solo.pipeline or self.config.roundtable.pipeline)
            return Lineup((members[0].id,), None, pipeline)
        coordinator = (
            by_id[coordinator_id] if coordinator_id else self.pick_coordinator(pick, members)
        )
        return Lineup(tuple(member_ids), coordinator.id, tuple(self.config.roundtable.pipeline))
