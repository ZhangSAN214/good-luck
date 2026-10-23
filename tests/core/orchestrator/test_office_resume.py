"""办公室模式中途断掉（进程退出、断网）后继续：已完成的发言、项目组的工作不会重做。"""

from __future__ import annotations

import pytest

from roundtable.core.routing import Question, UserChoice
from roundtable.core.steps import restore_state

from .conftest import MEDIUM, Env


class Crash(Exception):
    pass


def crash_once(env: Env, marker: str) -> None:
    """第一次遇到系统提示含 marker 的调用时中断。"""
    original = env.fake._default
    crashed = {"done": False}

    def flaky(model, messages):
        if marker in messages[0].content and not crashed["done"]:
            crashed["done"] = True
            raise Crash("进程中断")
        return original(model, messages)

    env.fake._default = flaky


def office_env(mode: str) -> Env:
    env = Env(confirm_threshold_usd=100.0)
    env.office_mode = mode
    return env


async def start(env: Env) -> str:
    with pytest.raises(Crash):
        await env.orc.start(Question(MEDIUM), UserChoice(workflow="office"), seed=3)
    [summary] = env.rt.repo.list_sessions()
    return summary.id


async def test_meeting_resumes_at_the_decision_without_repeating_speeches():
    env = office_env("meeting")
    crash_once(env, "会议纪要")
    sid = await start(env)
    assert len(restore_state(env.rt.repo, sid, 0).office.speeches) == 2
    speeches = len(env.models_called("参会人之一"))

    r = await env.orc.resume(sid)
    assert r.status == "completed"
    assert len(env.models_called("参会人之一")) == speeches  # 发言没有重复
    assert restore_state(env.rt.repo, sid, 0).office.final.startswith("## 结论")


async def test_project_resumes_at_the_review_without_redoing_the_team_work():
    env = office_env("project")
    crash_once(env, "最终验收")
    sid = await start(env)
    assert len(env.rt.repo.tables(sid)) == 2
    before = [c.model for c in env.fake.calls if "最终验收" not in c.messages[0].content]

    r = await env.orc.resume(sid)
    assert r.status == "completed"
    after = [c.model for c in env.fake.calls if "最终验收" not in c.messages[0].content]
    assert after == before  # 项目组的工作、分诊都没有重做
    reviews = [c for c in env.rt.repo.session_view(sid).calls if c.step == "office_review"]
    assert [c.failed for c in reviews].count(False) == 1
    assert len(env.rt.repo.tables(sid)) == 2  # 不会再开一张项目组桌子
