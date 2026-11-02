"""对账（成长 G2）：口头禅老歇着、学来的说法转正、养成的习惯和档案对不上时，写成「对账」经历
放进那一周的反思材料（可引用）；只读，不改档案。"""

from __future__ import annotations

import json
from datetime import UTC, date

from roundtable.core.member.drift import KEY
from roundtable.core.member.growth import build
from roundtable.core.member.growth.reconcile import notes
from roundtable.core.member.growth.timeline import GrowthLog
from roundtable.core.member.profile import ProfileStore

from ...orchestrator.conftest import Env
from .common import MONDAY, setup


def drift_state(env, member, swaps, promoted=None):
    data = {"swaps": swaps, "promoted": promoted or {}}
    env.rt.repo.set_setting(KEY + member, json.dumps(data, ensure_ascii=False))


def test_mismatches_become_reflection_lines():
    env = Env()
    setup(env)
    rest = {"kind": "rest", "old": "V1的口头禅", "new": "", "why": "腻了"}
    drift_state(
        env,
        "V1",
        [rest | {"at": "2026-09-20T10:00:00+00:00"}, rest | {"at": "2026-10-20T10:00:00+00:00"}],
        {"绝了": {"from": "V2", "at": "2026-10-10T10:00:00+00:00"}, "笑死": {"from": "user"}},
    )
    log = GrowthLog(env.rt)
    blob = log.load("V1")
    blob["counts"] = {"常去:天台": {"n": 9, "weeks": 4, "last": "2026-W43"}}
    log.save("V1", blob)
    got = notes(env.rt, "V1", MONDAY, date(2026, 10, 26))
    assert any("「V1的口头禅」最近说腻了 2 回" in x for x in got)
    assert any("跟V2学的「绝了」" in x for x in got)
    assert any("跟用户学的「笑死」" in x for x in got)
    assert any("常去天台" in x for x in got)
    before = ProfileStore(env.rt).current("V1").version
    week = build(env.rt, "V1", MONDAY, UTC)
    recon = [ln for ln in week.lines if ln.source == "recon"]
    assert recon and all(ln.day == "2026-10-25" for ln in recon)
    assert [ln.user for ln in recon if "笑死" in ln.text] == [True]  # 跟你学的算受你影响
    assert "对账" in week.text()
    assert ProfileStore(env.rt).current("V1").version == before  # 只读


def test_matching_profile_has_nothing_to_reconcile():
    env = Env()
    setup(env)
    assert notes(env.rt, "V1", MONDAY, date(2026, 10, 26)) == []
    rules = env.rt.config.roundtable.chat.engine.growth.model_copy(update={"reconcile": False})
    assert rules.reconcile is False
