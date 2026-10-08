"""揭晓前的视图不得包含任何模型身份：模型 id、厂商、别称、渠道、阵容、规划员模型。"""

from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from roundtable.core.allocation import IdentityScrubber
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import ErrorKind, FakeProvider, KeyRing, redact
from roundtable.core.routing import Question, route_question
from roundtable.core.storage import HIDDEN_ERROR

from .conftest import CONFIG, MSG

SCRUBBER = IdentityScrubber.from_config(CONFIG.models)
QUESTION = "求函数 f(x)=x^3-3x 在 [-2,2] 上的最值，并说明理由。"


def identity_terms() -> set[str]:
    terms = set(CONFIG.models.channels)
    for m in CONFIG.models.models:
        terms |= {m.id, m.vendor, *m.aliases, *(r.model for r in m.routes)}
    return {t.lower() for t in terms}


def leaked(view) -> list[str]:
    text = json.dumps(asdict(view), ensure_ascii=False).lower()
    return sorted(t for t in identity_terms() if t in text)


async def populated(repo, router, *, member_output="答案是 2。"):
    """跑一遍"规划 + 一次组员调用 + 一次失败调用"，把各类数据都写进库。"""
    q = Question(QUESTION)
    d = await route_question(q, None, config=CONFIG, router=router, prompts=PromptLibrary(), seed=4)
    sid = repo.create_session(q.text, seed=d.seed)
    repo.save_routing(sid, d.record(q))
    repo.record_planner(sid, d.assessment.planner)
    codes = dict(zip(["甲", "乙", "丙"], d.lineup.members, strict=False))
    repo.add_seats(sid, 0, codes, coordinator=d.lineup.coordinator)

    member = d.lineup.members[0]
    c = await router.complete(member, MSG)
    call = repo.record_call(
        sid,
        step="answer",
        role="member",
        model_id=member,
        messages=MSG,
        table_no=0,
        code="甲",
        completion=c,
    )
    repo.save_output(
        sid,
        table_no=0,
        step="answer",
        kind="answer",
        code="甲",
        content=member_output,
        call_id=call,
    )

    from roundtable.core.providers import AllChannelsFailed, ChannelRouter

    failing = ChannelRouter(
        CONFIG.models,
        {n: FakeProvider(n, default=ErrorKind.SERVER) for n in CONFIG.models.channels},
        policy=CONFIG.roundtable.request.model_copy(update={"failover_rounds": 1}),
    )
    with pytest.raises(AllChannelsFailed) as info:
        await failing.complete(member, MSG)
    repo.record_call(
        sid,
        step="answer",
        role="member",
        model_id=member,
        messages=MSG,
        table_no=0,
        code="乙",
        failure=info.value,
    )
    repo.set_status(sid, "failed", error=str(info.value))
    return sid, d


async def test_view_before_reveal_has_no_identity(repo, router):
    sid, d = await populated(repo, router)
    view = repo.session_view(sid, scrub=SCRUBBER.scrub)
    assert not view.revealed
    assert leaked(view) == []
    assert all(s.model_id is None for s in view.seats)
    assert all(c.model_id is None and c.channel is None and c.attempts == () for c in view.calls)
    assert "members" not in view.routing and "planner_model" not in view.routing
    assert view.routing["plan"] == d.plan  # 非身份信息照常可见
    failed = [c for c in view.calls if c.failed]
    assert failed and all(c.error == HIDDEN_ERROR for c in failed)
    assert view.error == HIDDEN_ERROR


async def test_view_after_reveal_shows_identity(repo, router):
    sid, d = await populated(repo, router)
    repo.mark_revealed(sid)
    view = repo.session_view(sid, scrub=SCRUBBER.scrub)
    assert view.revealed
    assert {s.model_id for s in view.seats if s.role == "member"} == set(d.lineup.members)
    member_calls = [c for c in view.calls if c.role == "member" and not c.failed]
    assert member_calls[0].channel is not None and member_calls[0].attempts
    assert view.routing["members"] == list(d.lineup.members)
    assert "[" in [c for c in view.calls if c.failed][0].error  # 原始错误（含渠道）


async def test_self_identification_in_output_scrubbed_before_reveal(repo, router):
    sid, _ = await populated(repo, router, member_output="作为 Claude，我认为答案是 2。")
    before = repo.session_view(sid, scrub=SCRUBBER.scrub)
    assert "Claude" not in before.outputs[0].content
    repo.mark_revealed(sid)
    after = repo.session_view(sid, scrub=SCRUBBER.scrub)
    assert "Claude" in after.outputs[0].content  # 揭晓后显示原文


async def test_list_sessions_has_no_identity(repo, router):
    await populated(repo, router)
    text = json.dumps([asdict(s) for s in repo.list_sessions()], ensure_ascii=False).lower()
    assert [t for t in identity_terms() if t in text] == []


async def test_database_contains_no_keys(repo):
    """带着真实格式的 key 跑一遍，数据库全文中不得出现任何已登记的密钥或疑似密钥。"""
    from roundtable.core.providers import ChannelRouter, build_providers

    fake_keys = {
        "OPENROUTER_API_KEY": "sk-or-v1-" + "e" * 48,
        "GEMINI_API_KEY": "AIza" + "k" * 35,
    }
    keys = KeyRing.from_env(list(fake_keys), environ=fake_keys)
    providers, unavailable = build_providers(CONFIG.models, keys, 30)
    # 用 Fake 替换真实适配器（不联网），key 已经登记到脱敏表
    router = ChannelRouter(
        CONFIG.models, {n: FakeProvider(n) for n in providers}, unavailable=unavailable
    )
    await populated(repo, router)
    dump = "\n".join(repo.conn.iterdump())
    for value in fake_keys.values():
        assert value not in dump
    assert redact(dump) == dump
