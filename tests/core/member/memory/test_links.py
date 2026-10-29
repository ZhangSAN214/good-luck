"""联想网：赫布学习、扩散激活、衰减与上限、夜间随机游走（可复现）。"""

from __future__ import annotations

import pytest

from roundtable.core.member.memory import LinkNet

from ...orchestrator.conftest import Env


def items(env: Env, n: int) -> list[int]:
    repo = env.rt.repo
    return [repo.add_memory_item("V1", f"事{i}", day="2026-10-20")["id"] for i in range(n)]


def weight(env: Env, a: int, b: int) -> float:
    a, b = min(a, b), max(a, b)
    return next(w for x, y, w in env.rt.repo.memory_links_of("V1", [a]) if (x, y) == (a, b))


def test_hebbian_strengthens_towards_one():
    env = Env()
    a, b, c = items(env, 3)
    net = LinkNet(env.rt)
    assert net.strengthen("V1", [a, b, c]) == 3
    assert weight(env, a, b) == pytest.approx(0.2)
    net.strengthen("V1", [a, b])
    assert weight(env, a, b) == pytest.approx(0.36)  # 0.2 + 0.2 × 0.8
    assert weight(env, b, c) == pytest.approx(0.2)


def test_spread_reaches_neighbours_with_decay():
    env = Env()
    a, b, c, d = items(env, 4)
    net = LinkNet(env.rt)
    for _ in range(5):
        net.strengthen("V1", [a, b])
        net.strengthen("V1", [b, c])
    act = net.spread("V1", {a: 1.0})
    assert set(act) == {b, c} and act[b] > act[c] > 0  # 两步之内，越远越弱
    assert d not in act


def test_decay_prunes_and_links_per_item_are_capped():
    env = Env()
    ids = items(env, 30)
    net = LinkNet(env.rt)
    for other in ids[1:]:
        net.strengthen("V1", [ids[0], other])
    assert len(env.rt.repo.memory_links_of("V1", [ids[0]])) == 20  # 每件最多 20 条
    removed = sum(net.decay("V1") for _ in range(80))  # 0.2 × 0.98^80 < 0.05
    assert removed > 0 and env.rt.repo.count_memory_links("V1") == 0


def test_walk_is_reproducible_and_follows_links():
    env = Env()
    a, b, c = items(env, 3)
    net = LinkNet(env.rt)
    net.strengthen("V1", [a, b])
    net.strengthen("V1", [b, c])
    paths = net.walk("V1", [a], "walk:V1:2026-10-20")
    assert paths and all(p[0] == a for p in paths)
    assert all(p[1] == b for p in paths)
    env2 = Env()
    a2, b2, c2 = items(env2, 3)
    net2 = LinkNet(env2.rt)
    net2.strengthen("V1", [a2, b2])
    net2.strengthen("V1", [b2, c2])
    assert net2.walk("V1", [a2], "walk:V1:2026-10-20") == paths  # 同一成员、同一天结果一样
