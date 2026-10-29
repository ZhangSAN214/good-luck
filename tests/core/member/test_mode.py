"""工作与生活分开：工作模式不挂载成员引擎，发给模型的每条消息都不含成员模块的内容；
生活模式只读工作经历（匿名未揭晓的不读），不反写工作。"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from roundtable.core.member import Mode, WorkModeError, mode_of, require_life
from roundtable.core.member.bus import Event
from roundtable.core.member.context import Piece
from roundtable.core.member.engine import LAST_KEY, MemberEngine
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from ..orchestrator.conftest import MEDIUM, Env

MARK = "引擎标记ZX9"
SRC = Path(__file__).resolve().parents[3] / "src" / "roundtable" / "core"
WORK_PACKAGES = ("orchestrator", "steps", "routing", "allocation", "prompts", "office")


def test_mode_of_workflows():
    assert mode_of("chat") is Mode.LIFE
    for wf in ("discussion", "collab", "office", "solo", "brainstorm", "auto"):
        assert mode_of(wf) is Mode.WORK
    require_life("life")
    with pytest.raises(WorkModeError):
        require_life(Mode.WORK)


def test_engine_context_refuses_work_mode():
    engine = MemberEngine(Env().rt, lambda: [])
    with pytest.raises(WorkModeError):
        engine.context([Piece("card", "x", fixed=True)], mode="work")
    assert engine.context([Piece("card", "x", fixed=True)]).text == "x"


def test_work_code_never_imports_member_engine():
    # 成员引擎和日历底座（只影响生活）都不进工作模式
    pattern = re.compile(r"roundtable\.core\.(member|almanac)\b|from \.+(member|almanac)\b")
    for pkg in WORK_PACKAGES:
        for f in (SRC / pkg).rglob("*.py"):
            assert not pattern.search(f.read_text(encoding="utf-8")), f


@pytest.mark.parametrize("workflow", ["discussion", "office", "solo", "collab"])
async def test_member_modules_never_reach_work_prompts(workflow):
    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    for m in svc.chats.everyone():
        for sec in ("identity", "personality", "values", "style", "likes", "state"):
            svc.engine.profiles.change(m.id, {sec: {"标记": MARK}}, author="user")
        svc.engine.bus.publish(Event("perceive.video", datetime.now(UTC), m.id, {"card": MARK}))
    await svc.engine.tick(datetime.now(UTC))
    sid = svc.create(MEDIUM, seed=3, workflow=workflow)
    async for _ in svc.events(sid):
        pass
    assert env.fake.calls
    sent = json.dumps([[m.content for m in c.messages] for c in env.fake.calls], ensure_ascii=False)
    assert MARK not in sent


def test_life_reads_work_done_but_not_anonymous_unrevealed():
    env = Env()
    now = datetime(2026, 10, 29, 15, tzinfo=UTC)
    engine = MemberEngine(env.rt, lambda: [])
    models = [m for m in env.config.models.models if m.seat][:3]
    rows = [
        {"model_id": models[0].id, "session_id": "s1", "question": "求极值", "anonymous": 0,
         "revealed_at": None, "at": iso(now - timedelta(minutes=30))},
        {"model_id": models[1].id, "session_id": "s2", "question": "匿名题", "anonymous": 1,
         "revealed_at": None, "at": iso(now - timedelta(minutes=20))},
        {"model_id": models[2].id, "session_id": "s3", "question": "揭晓题", "anonymous": 1,
         "revealed_at": iso(now), "at": iso(now - timedelta(minutes=10))},
    ]  # fmt: skip
    env.rt.repo.office_work_between = lambda start, end: rows
    got = []
    engine.bus.subscribe("work.done", got.append)
    assert engine.bridge_work(now - timedelta(hours=1), now) == 2
    assert [e.data["question"] for e in got] == ["求极值", "揭晓题"]
    assert got[0].member == models[0].vendor


async def test_engine_tick_bridges_work_into_events():
    env = Env()
    now = datetime(2026, 10, 29, 15, tzinfo=UTC)
    engine = MemberEngine(env.rt, lambda: [])
    model = next(m for m in env.config.models.models if m.seat)
    env.rt.repo.office_work_between = lambda start, end: [
        {"model_id": model.id, "session_id": "s1", "question": "题", "anonymous": 0,
         "revealed_at": None, "at": iso(now - timedelta(minutes=5))}
    ]  # fmt: skip
    env.rt.repo.set_setting(LAST_KEY, (now - timedelta(hours=1)).isoformat())
    assert (await engine.tick(now))["events"] == 1
