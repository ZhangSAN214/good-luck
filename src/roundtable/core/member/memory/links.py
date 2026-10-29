"""联想网（P2；用户 2026-10-29 批准的 M22 档 1 的记忆部分）。全部由程序算，不调用模型。

- **赫布学习**：两件记忆一起出现（同一天整理出来、同一次被想起）就连得更紧：
  ``w ← w + learn_rate × (1 − w)``；每晚所有连接衰减 ``nightly_decay``，太弱的删掉，
  每件最多留 ``max_links`` 条最强的（连接数有上限，记忆再多也不会爆）。
- **扩散激活**：召回时从分数最高的几件出发，顺着连接走 ``hops`` 步，每步激活乘以
  ``强度 × spread``；激活最高、本来没挑上的几件作为「联想到的」一起带上（``assoc_max``）。
- **夜间随机游走**：每晚从当天的记忆出发，按连接强度随机走几步（随机数由成员和日期决定，可复现），
  走到的旧事交给夜间整理，可能写进日记（「忽然想起」）；走过的路也轻轻加强一点（回放巩固）。
- 每次更新都记进训练数据（只存本机、不过期），以后可能用来训练本地小模型（档 2）。
- 所有成员的参数完全相同（中立）。
"""

from __future__ import annotations

import itertools
import random
from collections.abc import Iterable
from typing import Any

MAX_TOGETHER = 8  # 一次最多让几件互相加强（两两之间，最多 28 条）


def _pair(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


class LinkNet:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.memory.links

    @property
    def repo(self) -> Any:
        return self.rt.repo

    def strengthen(self, member: str, ids: Iterable[int], rate: float | None = None) -> int:
        """这些件两两加强（赫布）。返回更新了几条连接。"""
        if not self.rules.enabled:
            return 0
        ids = sorted(set(ids))[:MAX_TOGETHER]
        if len(ids) < 2:
            return 0
        lr = self.rules.learn_rate if rate is None else rate
        current = {_pair(a, b): w for a, b, w in self.repo.memory_links_of(member, ids)}
        updates = {}
        for a, b in itertools.combinations(ids, 2):
            w = current.get((a, b), 0.0)
            updates[(a, b)] = w + lr * (1 - w)
        self.repo.set_memory_links(member, updates)
        for i in ids:
            self.repo.trim_memory_links(member, i, self.rules.max_links)
        return len(updates)

    def neighbors(self, member: str, ids: Iterable[int]) -> dict[int, list[tuple[int, float]]]:
        out: dict[int, list[tuple[int, float]]] = {}
        for a, b, w in self.repo.memory_links_of(member, list(ids)):
            out.setdefault(a, []).append((b, w))
            out.setdefault(b, []).append((a, w))
        return out

    def spread(self, member: str, seeds: dict[int, float]) -> dict[int, float]:
        """从 seeds（件 → 初始激活）扩散 hops 步，返回种子以外各件得到的激活。"""
        if not self.rules.enabled or not seeds:
            return {}
        act = dict(seeds)
        frontier = dict(seeds)
        for _ in range(self.rules.hops):
            nb = self.neighbors(member, frontier)
            nxt: dict[int, float] = {}
            for src, a in frontier.items():
                for dst, w in nb.get(src, []):
                    gain = a * w * self.rules.spread
                    if gain > act.get(dst, 0.0):
                        nxt[dst] = max(nxt.get(dst, 0.0), gain)
            for k, v in nxt.items():
                act[k] = max(act.get(k, 0.0), v)
            frontier = nxt
            if not frontier:
                break
        return {k: v for k, v in act.items() if k not in seeds}

    def walk(self, member: str, starts: list[int], seed: str) -> list[list[int]]:
        """夜间随机游走：从 starts 中随机挑起点，按强度走 walk_steps 步。返回每次走过的路。"""
        if not self.rules.enabled or not starts:
            return []
        rng = random.Random(seed)
        paths = []
        for _ in range(self.rules.walks):
            node = rng.choice(sorted(starts))
            path = [node]
            for _ in range(self.rules.walk_steps):
                options = [(n, w) for n, w in self.neighbors(member, [node]).get(node, [])]
                options = sorted(o for o in options if o[0] not in path)
                if not options:
                    break
                node = rng.choices([n for n, _ in options], weights=[w for _, w in options])[0]
                path.append(node)
            if len(path) > 1:
                paths.append(path)
                for a, b in itertools.pairwise(path):  # 回放巩固：走过的路轻轻加强
                    self.strengthen(member, (a, b), self.rules.learn_rate / 4)
        return paths

    def decay(self, member: str) -> int:
        if not self.rules.enabled:
            return 0
        return self.repo.decay_memory_links(
            member, 1 - self.rules.nightly_decay, self.rules.min_weight
        )
