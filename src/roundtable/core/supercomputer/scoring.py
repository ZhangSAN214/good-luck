"""派活打分：只看配置里的数字和当前状态，不看是谁（换名后结果不变，有测试）。

分数 = 擅长 × skill + 今天剩余次数比例 × quota − 手上的活 × busy。
不可用、冷却中、今天次数用完的不参加；同分时用可复现的随机数决定。
``weights.metered_last`` 打开时按档派：包月订阅 → API 额度（via=credit）→ 按用量花钱（metered），
前一档都不能参加才轮到下一档。后两档照样打分、列在排名里（标 standby），但排在前一档后面。
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
    standby: bool = False  # 不是包月的：排在包月后面，只补位
    credit: bool = False  # 走预付 / 赠送的 API 额度（界面上的说明不同）

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
        if self.credit:
            out["credit"] = True
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
    for w in eligible:
        s = score(w, kind, states[w.id], weights)
        standby = weights.metered_last and w.tier > 0
        if standby:  # 面板上标「补位」：排在后面时、或前一档都用不了而轮到它时
            s = Score(s.worker, s.total, s.skill, s.quota, s.busy, True, w.tier == 1)
        group = -w.tier if weights.metered_last else 0
        scored.append((group, s.total, rng.random(), w, s))
    scored.sort(key=lambda t: (t[0], t[1], t[2]), reverse=True)
    ranking = [t[4] for t in scored]
    return (scored[0][3] if scored else None), ranking
