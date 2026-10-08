"""组建阵容：所选范围内的每个可用模型都上桌，其中一个担任统筹（本题不作答），其余为组员。

只看档位，不看模型名或厂商；统筹按轮换规则选出（随机 + 最近当过的排后）。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from roundtable.core.allocation import NotEnoughModels
from roundtable.core.config import AppConfig, ModelSpec
from roundtable.core.config.schema import PlanSpec


@dataclass(frozen=True)
class Lineup:
    members: tuple[str, ...]
    coordinator: str | None
    pipeline: tuple[str, ...]
    # 属于所选范围、但当前没有可用渠道而无法上桌的模型
    absent: tuple[str, ...] = ()


class LineupBuilder:
    def __init__(
        self,
        config: AppConfig,
        available: Sequence[ModelSpec],
        rng: random.Random,
        *,
        recent_coordinators: Sequence[str] = (),
    ) -> None:
        self.config = config
        self.available = list(available)
        self.rng = rng
        self.recent_coordinators = list(recent_coordinators)

    def pick_coordinator(self, pool: Sequence[ModelSpec]) -> ModelSpec:
        """统筹：fixed 指定的模型在场时用它；否则随机，最近当过统筹的排后。"""
        rule = self.config.roundtable.coordinator
        if rule.strategy == "fixed":
            fixed = [m for m in pool if m.id == rule.fixed_id]
            if fixed:
                return fixed[0]
        candidates = list(pool)
        self.rng.shuffle(candidates)
        if rule.strategy != "random":
            # recent_coordinators 按"最近的在前"排列：没当过的在前，越近当过的越靠后
            recent = self.recent_coordinators
            candidates.sort(key=lambda m: len(recent) - recent.index(m.id) if m.id in recent else 0)
        return candidates[0]

    def _seat(
        self,
        pool: Sequence[ModelSpec],
        pipeline: Sequence[str],
        *,
        coordinator: str | None = None,
        absent: Sequence[str] = (),
        scope: str,
    ) -> Lineup:
        rt = self.config.roundtable
        need = rt.min_members + 1
        if len(pool) < need:
            missing = f"（缺席：{', '.join(absent)}）" if absent else ""
            raise NotEnoughModels(
                f"{scope}只有 {len(pool)} 个可用模型，至少需要 {need} 个"
                f"（{rt.min_members} 个组员 + 1 个统筹）{missing}"
            )
        by_id = {m.id: m for m in pool}
        chosen = by_id[coordinator] if coordinator else self.pick_coordinator(pool)
        members = tuple(m.id for m in pool if m.id != chosen.id)
        if len(members) > rt.seats:
            raise NotEnoughModels(
                f"{scope}有 {len(members)} 个组员，超过座位数 {rt.seats}"
                "（roundtable.yaml 的 seats）"
            )
        return Lineup(members, chosen.id, tuple(pipeline), tuple(absent))

    def build(self, plan: PlanSpec) -> Lineup:
        """档位全员：该档位的所有可用模型上桌。"""
        tiers = set(plan.tiers)
        pool = [m for m in self.available if m.tier in tiers]
        seated = {m.id for m in pool}
        absent = [
            m.id for m in self.config.models.enabled if m.tier in tiers and m.id not in seated
        ]
        pipeline = plan.pipeline or self.config.roundtable.pipeline
        return self._seat(pool, pipeline, absent=absent, scope=f"「{plan.label}」")

    def build_custom(self, model_ids: Sequence[str], coordinator_id: str | None) -> Lineup:
        """自选：勾选的模型全部上桌；统筹可指定（必须是勾选的模型之一），否则按规则选。"""
        by_id = {m.id: m for m in self.available}
        pool = [by_id[i] for i in model_ids]
        return self._seat(
            pool, self.config.roundtable.pipeline, coordinator=coordinator_id, scope="自选阵容"
        )
