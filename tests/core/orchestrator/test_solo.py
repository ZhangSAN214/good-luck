"""个人作答（R13）：只请一位成员直接回答，没有统筹、互评和汇总。"""

from __future__ import annotations

import pytest

from roundtable.core.routing import Question, RoutingError, UserChoice

from .conftest import SHORT, Env

ANSWER = "独立完成同一道题"


async def test_solo_runs_one_model_only_and_costs_less(env):
    solo = await env.orc.start(Question(SHORT), UserChoice("budget", workflow="solo"), seed=1)
    assert solo.status == "completed"
    assert env.models_called(ANSWER) == ["b1"]  # 只有最便宜的一位作答
    table = env.rt.repo.tables(solo.session_id)[0]
    assert table["coordinator"] is None and list(table["members"].values()) == ["b1"]
    assert table["pipeline"] == ["answer", "reveal"]
    assert solo.final_answer

    full = Env()
    discussion = await full.orc.start(Question(SHORT), seed=1)
    assert solo.cost_usd < discussion.cost_usd


def test_solo_rejects_media_and_bad_custom():
    with pytest.raises(RoutingError):
        UserChoice("budget", workflow="solo", media="image")
    with pytest.raises(RoutingError):
        UserChoice("custom", ("b1", "b2"), None, "solo")


async def test_solo_anonymous_hides_identity_until_reveal(env):
    r = await env.orc.start(
        Question(SHORT), UserChoice("budget", workflow="solo"), seed=2, anonymous=True
    )
    assert r.status == "completed"
    view = env.rt.repo.session_view(r.session_id)
    assert "'b1'" not in str(view) and "V1" not in str(view)  # 揭晓前不出现模型 id
