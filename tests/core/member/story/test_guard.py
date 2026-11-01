"""单向隔离与不越权（L0 守卫）：

- 生活里的心情、关系、擅长、经历从不进办公室的派活、绩效和工作提示词：工作模式的代码不 import
  生活引擎、不读生活状态；跑一场讨论，发给模型的每条消息都不含生活事件和擅长。
- 生活引擎只发事件：不写档案（擅长由 P6 写）、不写心情 / 关系 / 记忆的表，只读自己的生活状态、
  生活事件和档案。
- 擅长的证据不看办公室表现：生活引擎不读办公室、会话和调用记录，也不读 work.done。
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

from roundtable.core.chat import ChatHub
from roundtable.core.growth import GrowthKeeper
from roundtable.core.member.story import store
from roundtable.core.service import RoundtableService
from roundtable.core.storage.repository import iso

from ...orchestrator.conftest import MEDIUM, Env
from .common import T0, likes, run_daily

SRC = Path(__file__).resolve().parents[4] / "src" / "roundtable"
STORY_PKG = SRC / "core" / "member" / "story"
WORK_PKGS = ("orchestrator", "steps", "routing", "allocation", "prompts", "office", "budget")


def test_work_code_never_touches_life_story():
    pattern = re.compile(r"member\.story|member_life|StoryEngine|Talents\b|likes\.擅长")
    for pkg in WORK_PKGS:
        for f in (SRC / "core" / pkg).rglob("*.py"):
            assert not pattern.search(f.read_text(encoding="utf-8")), f


def test_story_only_reads_and_writes_its_own_data():
    allowed = {"setting", "set_setting", "life_events_between"}
    for f in STORY_PKG.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        used = set(re.findall(r"\brepo\.(\w+)\(", text))
        assert used <= allowed, (f, used - allowed)
        assert ".change(" not in text, f  # 不写档案（擅长由 P6 写）
        # 擅长的证据不看办公室表现（上面的 repo 白名单已经不许读会话、调用、办公室记录）
        assert not re.search(r"[\"']work\.done[\"']|session_view|list_sessions", text), f


async def test_life_story_and_talents_never_reach_work_prompts():
    env = Env(confirm_threshold_usd=100.0)
    likes(env, "V1", 最爱=["星星", "天文"])
    for d in range(20):
        at = T0 + timedelta(days=d, hours=20)
        topic = "地点：公寓楼天台（看星星）"
        env.rt.repo.add_life_event(iso(at), "solo", ["V1"], place="rooftop", topic=topic)
    got = run_daily(env, 21, {f"V{i}": f"阿{i}" for i in range(1, 7)})
    awake = [h for h in got if h.story == "talent_awake"]
    assert awake  # 生活里确实有了擅长和生活事件
    GrowthKeeper(env.rt, ChatHub(env.rt).everyone).talents(T0 + timedelta(days=21))
    assert store.load(env.rt, "V1")["talents"]
    svc = RoundtableService(env.rt)
    n = len(env.fake.calls)
    sid = svc.create(MEDIUM, seed=3, workflow="discussion")
    async for _ in svc.events(sid):
        pass
    sent = "".join(m.content for c in env.fake.calls[n:] for m in c.messages)
    assert sent
    for h in got:
        assert h.text not in sent
    assert "擅长" not in sent and "天文科学" not in sent and "看星星" not in sent
