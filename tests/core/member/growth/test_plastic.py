"""发育期可塑性（成长 G2）：新来的照原规则；过了发育期每周最多 2 条、性格分隔够天数才动，
好奇的快、较真的慢；8 位同一套规则。"""

from __future__ import annotations

from datetime import date, timedelta

from roundtable.core.member.growth import Ledger
from roundtable.core.member.growth.plastic import plasticity
from roundtable.core.member.growth.timeline import GrowthLog
from roundtable.core.member.profile import ProfileStore

from ...orchestrator.conftest import Env
from .common import profile
from .test_gate import check, env_and_gate, score

JOIN = date(2026, 6, 1)


def joined(env, member="V1", scores=None, when=JOIN):
    env.rt.repo.clock = lambda: f"{when.isoformat()}T09:00:00+00:00"
    ProfileStore(env.rt).change(member, profile(member, scores), author="user", reason="入职")
    env.rt.repo.clock = lambda: "2026-10-26T09:00:00+00:00"


def test_young_member_keeps_the_old_rules():
    env = Env()
    joined(env, when=date(2026, 10, 1))
    p = plasticity(env.rt, "V1", date(2026, 10, 26), ProfileStore(env.rt).current("V1").data)
    assert p.young and p.max_changes == 3 and p.core_wait == 0 and "发育期" in p.line()


def test_settled_member_changes_slower_and_traits_set_the_pace():
    env = Env()
    joined(env, "V1", (3, 3, 3, 3, 3))
    today = date(2026, 10, 26)
    data = ProfileStore(env.rt).current("V1").data
    p = plasticity(env.rt, "V1", today, data)
    assert not p.young and p.max_changes == 2 and p.core_gap_days == 60 and p.core_wait == 0
    log = GrowthLog(env.rt)
    env.rt.repo.clock = lambda: (today - timedelta(days=20)).isoformat() + "T09:00:00+00:00"
    log.add(
        "V1",
        [
            {
                "week": "x",
                "kind": "change",
                "op": "score",
                "delta": 1,
                "path": "personality.五项.好奇",
                "user": False,
                "reverted": False,
            }
        ],
    )
    p = plasticity(env.rt, "V1", today, data)
    assert p.core_wait == 40 and "还要等 40 天" in p.line()
    curious = {"personality": {"五项": {"好奇": 5, "较真": 1, "外向": 3}}}
    stubborn = {"personality": {"五项": {"好奇": 1, "较真": 5, "外向": 3}}}
    fast = plasticity(env.rt, "V1", today, curious).core_gap_days
    slow = plasticity(env.rt, "V1", today, stubborn).core_gap_days
    assert fast < 60 < slow and fast >= 30  # 再快也至少隔 min_gap_days


def test_gate_blocks_score_while_waiting():
    env, gate = env_and_gate()
    v = check(env, gate, [score()], Ledger(core_wait=12))[0]
    assert not v.ok and "还要等 12 天" in v.reason
    assert check(env, gate, [score()], Ledger(core_wait=0))[0].ok
