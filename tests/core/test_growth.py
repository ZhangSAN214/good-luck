"""P6 成长：每周一用成员自己的模型读上一周、最多改 3 条，程序把关 + 另一位成员评审（不自评）后
才以 author=growth 写进档案；经历太少跳过并记原因；格式不对最多重试一次；额度不够下次再试；
只用于生活模式；只有成长插件写成长数据和 growth 档案版本。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roundtable.core.chat import ChatHub
from roundtable.core.growth import GrowthKeeper, seed_of, self_text
from roundtable.core.member import WorkModeError
from roundtable.core.member.profile import ProfileStore
from roundtable.core.service import RoundtableService, ServiceError

from .member.growth.common import (
    GOOD,
    MONDAY,
    NOW,
    WEEK,
    fake_growth,
    growth_env,
    is_reflect,
    is_review,
    proposal,
    setup,
)
from .orchestrator.conftest import Env

SRC = Path(__file__).resolve().parents[2] / "src" / "roundtable"


def keeper(env, folder=None) -> GrowthKeeper:
    return GrowthKeeper(env.rt, ChatHub(env.rt).everyone, folder=folder)


def calls(env, test):
    return [c for c in env.fake.calls if test(c.messages)]


async def test_weekly_tick_reflects_reviews_and_writes_profile(tmp_path):
    env = Env()
    setup(env)
    fake_growth(env)
    k = keeper(env, tmp_path)
    got = await k.tick(NOW)
    assert got == {"members": 6, "changes": 2, "rejected": 0, "skipped": 5, "reports": 0}
    (reflect,) = calls(env, is_reflect)  # 只有阿一经历够；其余 5 位跳过，不调用
    assert reflect.model == "b1" and "阿一" in reflect.messages[0].content
    assert "personality.五项：外向 3、较真 4、好奇 3" in reflect.messages[-1].content
    (review,) = calls(env, is_review)
    assert review.model != "b1"  # 不自评
    cur = ProfileStore(env.rt).current("V1")
    assert cur.get("personality.五项")["好奇"] == 4 and cur.get("likes.最爱") == ["红茶", "看星星"]
    hist = ProfileStore(env.rt).history("V1")
    assert [h["author"] for h in hist[:2]] == ["growth", "growth"]
    assert "成长 2026-W43" in hist[0]["reason"]
    tl = k.log.entries("V1")
    assert [e["kind"] for e in tl] == ["change", "change"] and tl[0]["score"] == 4
    assert tl[1]["quote"] == "越看越着迷" and tl[1]["evidence"] and tl[1]["version"]
    skip = k.log.entries("V2")[0]
    assert skip["kind"] == "skip" and "太少" in skip["reason"]
    assert (tmp_path / f"{WEEK}.json").exists()
    # 同一周不再反思
    n = len(env.fake.calls)
    assert (await k.tick(NOW + timedelta(days=2)))["members"] == 0 and len(env.fake.calls) == n


async def test_reviewer_is_seeded_and_never_self():
    env = Env()
    setup(env)
    fake_growth(env)
    await keeper(env).tick(NOW)
    import random

    others = [m for m in ChatHub(env.rt).everyone() if m.id != "V1"]
    expect = random.Random(seed_of(WEEK, "V1")).choice(sorted(others, key=lambda m: m.id))
    (review,) = calls(env, is_review)
    assert review.model == expect.model_id and expect.name in review.messages[0].content


async def test_low_review_score_blocks_change():
    env = Env()
    setup(env)
    fake_growth(env, score=2)
    got = await keeper(env).tick(NOW)
    assert got["changes"] == 0 and got["rejected"] == 2
    assert ProfileStore(env.rt).current("V1").get("personality.五项")["好奇"] == 3
    reasons = [e["reason"] for e in keeper(env).log.entries("V1")]
    assert any("评审只给了 2 分" in r for r in reasons) and "提议都没通过把关" in reasons


async def test_retry_once_with_feedback_when_not_json():
    env = Env()
    setup(env)
    fake_growth(env, reflect=["随便说几句，不是 JSON"])
    await keeper(env).tick(NOW)
    first, second = calls(env, is_reflect)
    assert "<feedback>\n没有\n</feedback>" in first.messages[-1].content
    assert "没有按格式" in second.messages[-1].content  # 第二次和第一次不同（不原样重试）
    assert ProfileStore(env.rt).current("V1").get("likes.最爱") == ["红茶", "看星星"]


async def test_retry_at_most_once_when_all_blocked():
    env = Env()
    setup(env)
    bad = json.dumps(
        {"changes": [{"op": "score", "path": "personality.五项.好奇", "delta": 3}]},
        ensure_ascii=False,
    )
    fake_growth(env, reflect=[bad, bad, bad])
    got = await keeper(env).tick(NOW)
    assert len(calls(env, is_reflect)) == 2 and not calls(env, is_review)
    assert "一次只能加 1 或减 1" in calls(env, is_reflect)[1].messages[-1].content
    assert got["changes"] == 0 and got["rejected"] == 1


async def test_nothing_to_change_is_recorded_and_not_retried():
    env = Env()
    setup(env)
    fake_growth(env, reflect=['{"changes": [], "skip": "这周过得很平常"}'])
    await keeper(env).tick(NOW)
    assert len(calls(env, is_reflect)) == 1 and not calls(env, is_review)
    assert keeper(env).log.entries("V1")[0]["reason"] == "这周过得很平常"


async def test_budget_exhausted_tries_again_next_tick():
    env = Env()
    setup(env)
    fake_growth(env)
    k = keeper(env)
    k.allow = lambda usd: False
    got = await k.tick(NOW)
    assert got["members"] == 5 and not calls(env, is_reflect)  # 阿一这周还算没反思
    assert k.log.done_week("V1") is None
    k.allow = lambda usd: True
    assert (await k.tick(NOW))["changes"] == 2


async def test_review_unanswered_changes_nothing():
    env = Env()
    setup(env)
    fake_growth(env)
    k = keeper(env)
    budget = iter([True, False])
    k.allow = lambda usd: next(budget, False)
    assert await k.run(ChatHub(env.rt).everyone()[0], MONDAY, UTC) is None
    assert ProfileStore(env.rt).current("V1").get("personality.五项")["好奇"] == 3


async def test_not_due_before_weekday_and_disabled():
    env = growth_env(weekday=3)
    setup(env)
    fake_growth(env)
    k = keeper(env)
    assert k.due(NOW.date()) is None and (await k.tick(NOW))["members"] == 0
    assert k.due(NOW.date() + timedelta(days=2)) == MONDAY
    env = growth_env(enabled=False)
    assert (await keeper(env).tick(NOW))["members"] == 0


async def test_same_rules_and_params_for_everyone():
    env = Env()
    setup(env)
    for m in ("V1", "V3"):  # 两位都有一样多的经历
        for day in ("2026-10-19", "2026-10-21", "2026-10-23"):
            env.rt.repo.add_memory(
                m, "day", "今天又去天文台看星星，越看越着迷。还看了流星。", period=day
            )
        env.rt.repo.add_memory_item(m, "在天文台待了一晚上看流星", day="2026-10-21")
    fake_growth(env)
    await keeper(env).tick(NOW)
    a, b = calls(env, is_reflect)
    assert a.params == b.params
    norm = lambda c: re.sub(r"阿.|V\d的口头禅|外向.*幽默 \d", "X", c.messages[0].content)  # noqa: E731
    assert norm(a) == norm(b)


async def test_work_mode_refused():
    env = Env()
    setup(env)
    with pytest.raises(WorkModeError):
        await keeper(env).run(ChatHub(env.rt).everyone()[0], MONDAY, UTC, mode="work")


def test_self_text_only_short_items():
    text = self_text(
        {"personality": {"五项": {"外向": 3}, "性格": "原文"}, "likes": {"最爱": ["a", "b"]}}
    )
    assert text == "personality.五项：外向 3\nlikes.最爱：a、b" and self_text({}) == "（没有）"


# --- 服务与接口 ----------------------------------------------------------------------


async def test_service_run_revert_and_life_tick():
    env = Env()
    setup(env)
    fake_growth(env)
    svc = RoundtableService(env.rt)
    svc._local_now = lambda: NOW
    got = await svc.member_growth_run("V1")
    assert got["result"]["changes"] == 2 and got["week"] == WEEK and got["due"] == WEEK
    with pytest.raises(ServiceError, match="已经反思过了"):
        await svc.member_growth_run("V1")
    entry = got["timeline"][0]
    after = svc.member_growth_revert("V1", entry["id"])
    assert after["timeline"][0]["kind"] == "revert"
    assert ProfileStore(env.rt).current("V1").get("likes.最爱") == ["红茶"]
    with pytest.raises(ServiceError, match="撤回过了"):
        svc.member_growth_revert("V1", entry["id"])
    with pytest.raises(ServiceError):
        svc.member_growth("没有这个人")
    # 生活每次推进顺带推进成长（别的成员这周跳过）
    await svc.life_tick()
    assert svc.growth.log.done_week("V2") == WEEK
    assert svc.member_growth_clear("V1")["deleted"] == 1


# --- 守卫 ----------------------------------------------------------------------------


def test_only_growth_writes_growth_profile_versions_and_data():
    growth_author = re.compile(r"author\s*=\s*(AUTHOR|[\"']growth[\"'])")
    for f in SRC.rglob("*.py"):
        if growth_author.search(f.read_text(encoding="utf-8")):
            assert f == SRC / "core" / "growth.py", f
    writer = re.compile(r"\.(save_growth_data|clear_growth_data)\(")
    for f in SRC.rglob("*.py"):
        if writer.search(f.read_text(encoding="utf-8")):
            assert f == SRC / "core" / "member" / "growth" / "timeline.py", f
    keys = re.compile(r"[\"']member_growth:")
    for f in SRC.rglob("*.py"):
        if keys.search(f.read_text(encoding="utf-8")):
            assert f.name == "repository.py", f


def test_work_code_never_imports_growth():
    pattern = re.compile(r"roundtable\.core\.growth\b|from \.+growth\b")
    for pkg in ("orchestrator", "steps", "routing", "allocation", "prompts", "office"):
        for f in (SRC / "core" / pkg).rglob("*.py"):
            assert not pattern.search(f.read_text(encoding="utf-8")), f


async def test_growth_never_reaches_work_prompts():
    from .orchestrator.conftest import MEDIUM

    env = Env(confirm_threshold_usd=100.0)
    setup(env)
    fake_growth(env)
    svc = RoundtableService(env.rt)
    svc._local_now = lambda: NOW
    await svc.member_growth_run("V1")
    n = len(env.fake.calls)
    sid = svc.create(MEDIUM, seed=3, workflow="discussion")
    async for _ in svc.events(sid):
        pass
    sent = "".join(m.content for c in env.fake.calls[n:] for m in c.messages)
    assert "看星星" not in sent and "成长" not in sent and "越看越着迷" not in sent


def test_good_fixture_is_valid():
    assert len(GOOD) == 2 and callable(proposal)
    assert datetime(2026, 10, 26).isoweekday() == 1


async def test_tick_counts_habits_from_all_events_and_writes_monthly_report():
    from roundtable.core.member.bus import Event, EventBus

    env = Env()
    setup(env)
    fake_growth(env)
    bus = EventBus(env.rt)
    for w in range(4):  # 9 月到 10 月，每周都有两次深夜说话
        for d in (0, 1):
            at = datetime(2026, 9, 28, 23, tzinfo=UTC) + timedelta(weeks=w, days=d)
            bus.publish(Event("chat.message", at, "V1", {"from_user": False}))
    k = keeper(env)
    got = await k.tick(datetime(2026, 11, 2, 12, tzinfo=UTC))
    assert got["reports"] == 1  # 只有阿一 10 月有经历
    (h,) = k.habits("V1")
    assert h["label"] == "常在深夜说话" and h["n"] == 8 and h["weeks"] == 4
    report = k.log.load("V1")["reports"]["2026-10"]
    assert report["diary_days"] == 4 and report["items"] == 2 and report["habits"] == [h["label"]]
    assert "表现得像" in report["note"]
    assert k.log.load("V1")["skills_at"]["娱乐"] == 1
    # 习惯和熟练度进了每周反思的材料
    await k.run(ChatHub(env.rt).everyone()[0], MONDAY, UTC)
    reflect = calls(env, is_reflect)
    assert reflect and "常在深夜说话（8 次，4 周）" in reflect[-1].messages[-1].content
    again = await k.tick(datetime(2026, 11, 3, 12, tzinfo=UTC))
    assert again["reports"] == 0 and k.habits("V1")[0]["n"] == 8  # 不重复计数


def test_service_exposes_habits_skills_and_reports():
    env = Env()
    setup(env)
    svc = RoundtableService(env.rt)
    got = svc.member_growth("V1")
    assert got["habits"] == [] and got["reports"] == []
    assert {s["domain"] for s in got["skills"]} == set(env.config.roundtable.chat.memory.domains)
