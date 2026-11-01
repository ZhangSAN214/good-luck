"""成长时间线：一条条撤回（性格分减回去、加的去掉、去掉的加回来，以你的身份存新版本），
撤回的不再计入每月格数和用户影响；每周本地备份只留最近几份。"""

from __future__ import annotations

import json
from datetime import date

import pytest

from roundtable.core.member.growth import GrowthLog, Proposal, Verdict
from roundtable.core.member.growth.timeline import CHANGE
from roundtable.core.member.profile import ProfileError, ProfileStore

from ...orchestrator.conftest import Env
from .common import WEEK, growth_env, profile, setup


def applied(env, log, p: Proposal, patch, *, user=False, old=None, new=None):
    store = ProfileStore(env.rt)
    version = store.change("V1", patch, author="growth", reason="测试").version
    v = Verdict(p, ok=True, patch=patch, old=old, new=new, user=user, evidence=["x"])
    log.add("V1", [log.entry(WEEK, v, kind=CHANGE, version=version)], week=WEEK)
    return log.entries("V1")[0]


def test_revert_each_kind_of_change():
    env = Env()
    setup(env, experiences=False)
    log, store = GrowthLog(env.rt), ProfileStore(env.rt)
    traits = {**profile("V1")["personality"]["五项"], "好奇": 4}
    e1 = applied(
        env,
        log,
        Proposal("score", "personality.五项.好奇", delta=1),
        {"personality": {"五项": traits}},
    )
    e2 = applied(
        env,
        log,
        Proposal("add", "likes.最爱", value="看星星"),
        {"likes": {"最爱": ["红茶", "看星星"]}},
    )
    e3 = applied(
        env,
        log,
        Proposal("remove", "personality.长处", value="耐心"),
        {"personality": {"长处": []}},
    )
    assert store.current("V1").get("personality.五项")["好奇"] == 4
    for e in (e1, e2, e3):
        log.revert("V1", e["id"])
    cur = store.current("V1")
    assert cur.get("personality.五项")["好奇"] == 3
    assert cur.get("likes.最爱") == ["红茶"] and cur.get("personality.长处") == ["耐心"]
    hist = store.history("V1")
    assert hist[0]["author"] == "user" and "撤回成长" in hist[0]["reason"]
    kinds = [e["kind"] for e in log.entries("V1")]
    assert kinds[:3] == ["revert"] * 3 and all(e["reverted"] for e in log.entries("V1")[3:])
    with pytest.raises(ProfileError, match="撤回过了"):
        log.revert("V1", e1["id"])
    with pytest.raises(ProfileError, match="没有这条"):
        log.revert("V1", 999)


def test_ledger_counts_month_and_window_but_not_reverted():
    env = Env()
    setup(env, experiences=False)
    log = GrowthLog(env.rt)
    traits = {**profile("V1")["personality"]["五项"], "好奇": 4}
    e = applied(
        env,
        log,
        Proposal("score", "personality.五项.好奇", delta=1),
        {"personality": {"五项": traits}},
        user=True,
    )
    today = date.fromisoformat(e["at"][:10])
    got = log.ledger("V1", today)
    assert (got.core_steps, got.total, got.user) == (1, 1, 1)
    log.revert("V1", e["id"])
    got = log.ledger("V1", today)
    assert (got.core_steps, got.total, got.user) == (0, 0, 0)


def test_backups_keep_latest_eight(tmp_path):
    env = growth_env(backups=8)
    setup(env, experiences=False)
    log = GrowthLog(env.rt)
    for w in range(30, 41):
        log.backup(tmp_path, f"2026-W{w}", ["V1", "V2"])
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [f"2026-W{w}.json" for w in range(33, 41)]
    data = json.loads((tmp_path / "2026-W40.json").read_text(encoding="utf-8"))
    assert data["members"]["V1"]["profile"]["likes"]["最爱"] == ["红茶"]
    assert log.backup(None, "2026-W41", ["V1"]) is None


def test_timeline_keep_and_clear():
    env = growth_env(timeline_keep=10)
    log = GrowthLog(env.rt)
    log.add("V1", [{"week": WEEK, "kind": "skip", "reason": str(i)} for i in range(15)], week=WEEK)
    assert len(log.entries("V1")) == 10 and log.entries("V1")[0]["reason"] == "14"
    assert log.done_week("V1") == WEEK
    assert log.clear("V1") == 1 and log.entries("V1") == [] and log.done_week("V1") is None
