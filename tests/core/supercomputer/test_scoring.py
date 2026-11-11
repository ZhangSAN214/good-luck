from __future__ import annotations

import random

import pytest

from roundtable.core.config.schema import SuperWeights, SuperWorker
from roundtable.core.supercomputer.scoring import WorkerState, pick, score

W = SuperWeights(skill=0.6, quota=0.3, busy=0.2)


def worker(wid, **skills):
    return SuperWorker(id=wid, label=wid.upper(), argv=["x"], skills=skills)


def test_score_formula():
    s = score(worker("a", code=0.9), "code", WorkerState(used=5, limit=20, running=1), W)
    assert s.skill == 0.9 and s.quota == 0.75 and s.busy == 1
    assert abs(s.total - (0.6 * 0.9 + 0.3 * 0.75 - 0.2)) < 1e-9
    assert score(worker("a"), "docs", WorkerState(), W).skill == 0.0


def test_pick_prefers_skill_then_quota_then_idle():
    ws = [worker("a", code=0.9), worker("b", code=0.6)]
    states = {"a": WorkerState(), "b": WorkerState()}
    chosen, ranking = pick(ws, "code", states, W, random.Random(1))
    assert chosen.id == "a" and [s.worker for s in ranking] == ["a", "b"]
    # a 今天快用完、手上还有两件 → 换 b
    states["a"] = WorkerState(used=19, running=2)
    assert pick(ws, "code", states, W, random.Random(1))[0].id == "b"


def test_excluded_workers():
    ws = [worker("a", code=0.9), worker("b", code=0.1)]
    states = {"a": WorkerState(used=20, limit=20), "b": WorkerState(blocked="没装")}
    chosen, ranking = pick(ws, "code", states, W, random.Random(1))
    assert chosen is None and ranking == []
    states["b"] = WorkerState()
    assert pick(ws, "code", states, W, random.Random(1))[0].id == "b"
    assert pick(ws, "code", states, W, random.Random(1), exclude=["b"])[0] is None


def test_rename_invariance():
    """把名字全换掉，同一个随机种子的选择完全一致（只看数字，不看是谁）。"""
    a = [worker("alpha", code=0.7, docs=0.7), worker("beta", code=0.7, docs=0.7)]
    b = [worker("zz1", code=0.7, docs=0.7), worker("zz2", code=0.7, docs=0.7)]
    for seed in range(20):
        sa = {"alpha": WorkerState(), "beta": WorkerState()}
        sb = {"zz1": WorkerState(), "zz2": WorkerState()}
        ca = pick(a, "code", sa, W, random.Random(seed))[0]
        cb = pick(b, "code", sb, W, random.Random(seed))[0]
        assert a.index(ca) == b.index(cb)


def metered(wid, **skills):
    return SuperWorker(id=wid, label=wid.upper(), argv=["x"], skills=skills, metered=True)


def test_metered_worker_only_fills_in():
    """2026-11-04 用户定：走订阅的先干，按用量花钱的只补位（就算它更擅长、更闲）。"""
    ws = [metered("paid", research=0.95), worker("a", research=0.5), worker("b", research=0.4)]
    states = {"paid": WorkerState(), "a": WorkerState(running=3), "b": WorkerState(running=3)}
    chosen, ranking = pick(ws, "research", states, W, random.Random(1))
    assert chosen.id == "a"
    assert [s.worker for s in ranking] == ["a", "b", "paid"]
    assert ranking[-1].as_dict()["standby"] is True and "standby" not in ranking[0].as_dict()
    # 走订阅的都用不了（次数用完、冷却）才轮到它
    states["a"] = WorkerState(used=20, limit=20)
    states["b"] = WorkerState(blocked="冷却中")
    chosen, ranking = pick(ws, "research", states, W, random.Random(1))
    assert chosen.id == "paid" and ranking[0].standby  # 面板显示「补位」
    # 被排除在外（比如不评自己的活）也算用不了
    states["b"] = WorkerState()
    assert pick(ws, "review", states, W, random.Random(1), exclude=["b"])[0].id == "paid"
    # 关掉 metered_last 就回到纯打分
    off = SuperWeights(skill=0.6, quota=0.3, busy=0.2, metered_last=False)
    states["a"] = WorkerState()
    assert pick(ws, "research", states, off, random.Random(1))[0].id == "paid"


def test_repo_config_marks_only_the_pay_per_use_worker():
    from roundtable.core.config import load_config

    sc = load_config().roundtable.supercomputer
    assert sc.weights.metered_last
    paid = [w for w in sc.workers if w.metered]
    assert [w.via for w in paid] == ["dsh"]  # dsh 按用量扣余额；命令行助手走订阅
    assert any(not w.metered for w in sc.workers)


def credit(wid, **skills):
    return SuperWorker(
        id=wid, label=wid.upper(), via="credit", credit_channel="ch", argv=["x"], skills=skills
    )


def test_credit_worker_sits_between_plan_and_metered():
    """2026-11-04 用户定：包月的先干，用 API 额度的其次，按用量花钱的（dsh）最后。"""
    ws = [metered("paid", code=0.99), credit("api", code=0.95), worker("plan", code=0.3)]
    states = {w.id: WorkerState() for w in ws}
    chosen, ranking = pick(ws, "code", states, W, random.Random(1))
    assert chosen.id == "plan"
    assert [s.worker for s in ranking] == ["plan", "api", "paid"]
    assert ranking[1].as_dict()["credit"] is True and ranking[1].standby
    assert "credit" not in ranking[2].as_dict()
    states["plan"] = WorkerState(blocked="冷却中")
    assert pick(ws, "code", states, W, random.Random(1))[0].id == "api"
    states["api"] = WorkerState(used=20, limit=20)
    assert pick(ws, "code", states, W, random.Random(1))[0].id == "paid"


def test_credit_worker_needs_channel_and_argv():
    with pytest.raises(ValueError, match="credit_channel"):
        SuperWorker(id="a", label="A", via="credit", argv=["x"])
    with pytest.raises(ValueError, match="argv"):
        SuperWorker(id="a", label="A", via="credit", credit_channel="ch")
    assert credit("a").tier == 1 and worker("b").tier == 0 and metered("c").tier == 2


def test_repo_config_workbench_does_not_use_api_credit():
    """2026-11-05 用户定「工作台不要使用 API」：名单里没有走 API 额度的助手。"""
    from roundtable.core.config import load_config

    cfg = load_config()
    assert not [w.id for w in cfg.roundtable.supercomputer.workers if w.via == "credit"]
