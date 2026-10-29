"""派活打分：只看配置里的数字和当前状态，不看是谁（换名后结果不变，有测试）。

分数 = 擅长 × skill + 今天剩余次数比例 × quota − 手上的活 × busy。
不可用、冷却中、今天次数用完的不参加；同分时用可复现的随机数决定。
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

    def as_dict(self) -> dict[str, float | str]:
        return {
            "worker": self.worker,
            "total": round(self.total, 3),
            "skill": round(self.skill, 3),
            "quota": round(self.quota, 3),
            "busy": round(self.busy, 3),
        }


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
    scored: list[tuple[float, float, SuperWorker, Score]] = []
    for w in workers:
        if w.id in exclude or excluded(states[w.id]):
            continue
        s = score(w, kind, states[w.id], weights)
        scored.append((s.total, rng.random(), w, s))
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    ranking = [t[3] for t in scored]
    return (scored[0][2] if scored else None), ranking
