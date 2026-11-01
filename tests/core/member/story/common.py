"""生活引擎（L0 / L10）测试的共用小工具。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from roundtable.core.member.profile import ProfileStore
from roundtable.core.member.story import StoryEngine

from ...orchestrator.conftest import Env

T0 = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)  # 星期一零点
NAMES = {f"V{i}": f"阿{'零一二三四五六七'[i]}" for i in range(1, 7)}


def story_env(**update: Any) -> Env:
    """改 chat.life.story 的几项（stories 可整个换掉；换了且没给 progress 时爱好进度关掉）。"""
    base = Env().config.roundtable.chat
    if not update:
        return Env()
    if "stories" in update and "progress" not in update:
        # 换了事件库的测试只看事件库：爱好进度（L1）关掉，免得混进来
        off = base.life.story.progress.model_copy(update={"enabled": False})
        update = {**update, "progress": off}
    story = base.life.story.model_copy(update=update)
    life = base.life.model_copy(update={"story": story})
    return Env(rt_update={"chat": base.model_copy(update={"life": life})})


def spec(**kw: Any) -> Any:
    from roundtable.core.config.schema import StorySpec

    return StorySpec(**kw)


def likes(env: Env, member: str, **items: Any) -> None:
    ProfileStore(env.rt).change(member, {"likes": items}, author="user", reason="测试")


def run_days(
    env: Env, days: int, names: dict[str, str] | None = None, start: datetime = T0
) -> list[Any]:
    """按半小时一次推进 days 天，返回全部到点的事。"""
    eng = StoryEngine(env.rt)
    out = []
    t = start
    while t < start + timedelta(days=days):
        out += eng.advance(t, t + timedelta(minutes=30), names or NAMES)
        t += timedelta(minutes=30)
    return out


def run_daily(env: Env, days: int, names: dict[str, str] | None = None) -> list[Any]:
    """每天推进一次（快）：(day, day + 1)。"""
    eng = StoryEngine(env.rt)
    out = []
    for i in range(days):
        a = T0 + timedelta(days=i)
        out += eng.advance(a, a + timedelta(days=1) - timedelta(seconds=1), names or NAMES)
    return out
