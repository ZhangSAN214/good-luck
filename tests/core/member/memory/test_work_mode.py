"""工作与生活分开：记忆与联想的内容（记忆件、挂着的事、习惯、日记）永远不进工作模式的提示词。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from roundtable.core.member.memory import MemoryStore
from roundtable.core.service import RoundtableService

from ...orchestrator.conftest import MEDIUM, Env

MARK = "记忆标记QW7"
SRC = Path(__file__).resolve().parents[4] / "src" / "roundtable" / "core"
WORK_PACKAGES = ("orchestrator", "steps", "routing", "allocation", "prompts", "office")


def test_work_code_never_imports_memory_modules():
    pattern = re.compile(r"roundtable\.core\.(member|memory|recall)\b")
    for pkg in WORK_PACKAGES:
        for f in (SRC / pkg).rglob("*.py"):
            assert not pattern.search(f.read_text(encoding="utf-8")), f


@pytest.mark.parametrize("workflow", ["discussion", "office", "solo", "collab"])
async def test_memories_never_reach_work_prompts(workflow):
    env = Env(confirm_threshold_usd=100.0)
    svc = RoundtableService(env.rt)
    s = MemoryStore(env.rt)
    for m in svc.chats.everyone():
        s.write(m.id, f"{MARK} 用户的偏好", day="2026-10-20", fact=True, importance=3)
        s.write(m.id, f"{MARK} 求函数最值那次", day="2026-10-20")
        s.hang(m.id, f"{MARK} 要记得的事", "2026-10-20")
        env.rt.repo.add_memory(m.id, "day", f"{MARK} 的日记", period="2026-10-20")
        env.rt.repo.set_memory_digest(m.id, f"常做的事：{MARK}", {})
    sid = svc.create(MEDIUM, seed=3, workflow=workflow)
    async for _ in svc.events(sid):
        pass
    assert env.fake.calls
    sent = json.dumps([[m.content for m in c.messages] for c in env.fake.calls], ensure_ascii=False)
    assert MARK not in sent
