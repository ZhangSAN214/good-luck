"""头脑风暴的无品牌偏好：把所有厂商和 id 换名后，同一 seed 下抽到的发散手法、碰撞杂交拿到的点子、
投票看到的顺序在位置上完全一致；同一步骤所有组员的请求参数相同。"""

from __future__ import annotations

import re

from roundtable.core.providers import ChannelRouter
from roundtable.core.steps import get_step

from ..orchestrator.test_brainstorm import cross_reply, ideate_reply, vote_reply
from ..routing.conftest import app_config
from .conftest import MEMBERS, Table
from .test_office_neutrality import RENAME, RENAMED_POOL

IDEATE, CROSS, VOTE = "其他人这一轮看不到你的点子", "让点子互相碰撞", "请投票"


def reply(model, messages):
    system = messages[0].content
    if IDEATE in system:
        return ideate_reply(model, messages)
    if CROSS in system:
        return cross_reply(model, messages)
    return vote_reply(model, messages)


def tables(seed: int) -> tuple[Table, Table]:
    a = Table(seed)
    b = Table(seed, members={c: RENAME[m] for c, m in MEMBERS.items()}, coordinator=RENAME["f1"])
    b.config = b.ctx.config = app_config(pool=RENAMED_POOL)
    for t in (a, b):
        t.fake._default = reply
        policy = t.config.roundtable.request.model_copy(update={"backoff_s": 0})
        t.router = t.ctx.router = ChannelRouter(t.config.models, {"c": t.fake}, policy=policy)
    return a, b


def seen(t: Table, phrase: str) -> dict[str, list[str]]:
    """每位组员（按代号）在这一步看到的点子编号顺序，或抽到的手法。"""
    out = {}
    for c in t.fake.calls:
        if phrase not in c.messages[0].content:
            continue
        code = re.search(r"代号「组员(.+?)」", c.messages[0].content).group(1)
        if phrase == IDEATE:
            out[code] = [re.search(r"你抽到的是「(.+?)」", c.messages[0].content).group(1)]
        else:
            out[code] = re.findall(r'<idea id="([IE]\d+)">', c.messages[-1].content)
    return out


async def run_steps(t: Table, seed: int = 0) -> None:
    for step in ("ideate", "crossbreed", "vote"):
        t.ctx.rng.seed(f"{seed}:{step}")  # 引擎每一步重新播种；这里两张桌子用同样的方式
        await get_step(step).run(t.ctx)


async def test_lenses_samples_and_vote_order_do_not_depend_on_names():
    lenses = set()
    for seed in range(5):
        a, b = tables(seed)
        await run_steps(a, seed)
        await run_steps(b, seed)
        for phrase in (IDEATE, CROSS, VOTE):
            x, y = seen(a, phrase), seen(b, phrase)
            assert x == y and len(x) == len(MEMBERS), phrase
        lenses |= {v[0] for v in seen(a, IDEATE).values()}
        assert a.ctx.state.brain.votes.keys() == b.ctx.state.brain.votes.keys()
    assert len(lenses) > len(MEMBERS)  # 不同 seed 抽到的手法不同


async def test_same_step_requests_share_params():
    a, _ = tables(0)
    await run_steps(a)
    for phrase in (IDEATE, CROSS, VOTE):
        params = {
            str(sorted(c.params.items())) for c in a.fake.calls if phrase in c.messages[0].content
        }
        assert len(params) == 1, phrase
