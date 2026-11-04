"""派活打分：只看配置里的数字和当前状态，不看是谁（换名后结果不变，有测试）。

分数 = 擅长 × skill + 今天剩余次数比例 × quota − 手上的活 × busy。
不可用、冷却中、今天次数用完的不参加；同分时用可复现的随机数决定。
``weights.metered_last`` 打开时，按用量花钱的（``metered``）只在走订阅的都不能参加时补位：
它们照样打分、列在排名里（标 standby），但排在所有走订阅的后面。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

from roundtable.core.config.schema import SuperWeights, SuperWorker


@dataclass
class WorkerState:
    used: int = 0  # 今天已派几件
    limit: int = 20  # 今天最多几件
    running: int = 0  # 手上正在干的（含排队）
    blocked: str | None = None  # 不可用的原因（没装、冷却中、dsh 到了月上限……）


@dataclass(frozen=True)
class Score:
    worker: str
    total: float
    skill: float
    quota: float
    busy: float
    standby: bool = False  # 按用量花钱、这次只补位

    def as_dict(self) -> dict[str, float | str | bool]:
        out: dict[str, float | str | bool] = {
            "worker": self.worker,
            "total": round(self.total, 3),
            "skill": round(self.skill, 3),
            "quota": round(self.quota, 3),
            "busy": round(self.busy, 3),
        }
        if self.standby:
            out["standby"] = True
        return out


def excluded(state: WorkerState) -> str | None:
    if state.blocked:
        return state.blocked
    if state.used >= state.limit:
        return f"今天的 {state.limit} 次用完了"
    return None


def score(worker: SuperWorker, kind: str, state: WorkerState, weights: SuperWeights) -> Score:
    skill = worker.skills.get(kind, 0.0)
    quota = max(0, state.limit - state.used) / state.limit if state.limit else 0.0
    busy = float(state.running)
    total = weights.skill * skill + weights.quota * quota - weights.busy * busy
    return Score(worker.id, total, skill, quota, busy)


def pick(
    workers: Sequence[SuperWorker],
    kind: str,
    states: dict[str, WorkerState],
    weights: SuperWeights,
    rng: random.Random,
    exclude: Sequence[str] = (),
) -> tuple[SuperWorker | None, list[Score]]:
    """挑分数最高的一位；返回（人选，所有参加者的分数从高到低）。没人可用时人选为 None。"""
    scored: list[tuple[int, float, float, SuperWorker, Score]] = []
    eligible = [w for w in workers if w.id not in exclude and not excluded(states[w.id])]
    free_ok = any(not w.metered for w in eligible)
    for w in eligible:
        s = score(w, kind, states[w.id], weights)
        standby = weights.metered_last and w.metered and free_ok
        if standby:
            s = Score(s.worker, s.total, s.skill, s.quota, s.busy, standby=True)
        scored.append((0 if standby else 1, s.total, rng.random(), w, s))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    ranking = [t[4] for t in scored]
    return (scored[0][3] if scored else None), ranking
