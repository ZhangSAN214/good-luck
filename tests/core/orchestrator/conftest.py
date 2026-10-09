from __future__ import annotations

import json

import pytest

from roundtable.core.orchestrator import Orchestrator
from roundtable.core.providers import FakeProvider
from roundtable.core.runtime import Runtime

from ..routing.conftest import app_config
from ..steps.conftest import default_reply, merge_reply, synthesis_reply

SHORT = "1+1=?"
MEDIUM = "求函数 f(x)=x^3-3x 在区间 [-2, 2] 上的最大值与最小值，并写出完整过程。"


def planner(difficulty: str = "medium"):
    def reply(model, messages):
        return json.dumps(
            {
                "task_type": "math",
                "difficulty": difficulty,
                "expected_answer_tokens": 600,
                "reason": "测试",
            }
        )

    return reply


class Env:
    def __init__(
        self, difficulty="medium", resolved=True, pool=None, with_media=False, **routing_overrides
    ):
        self.difficulty = difficulty
        self.resolved = resolved
        self.config = app_config(
            **({"pool": pool} if pool else {}), with_media=with_media, **routing_overrides
        )
        self.fake = FakeProvider("c", default=self.reply)
        policy = self.config.roundtable.request.model_copy(update={"backoff_s": 0})
        rt_cfg = self.config.roundtable.model_copy(update={"request": policy})
        self.config = self.config.model_copy(update={"roundtable": rt_cfg})
        self.rt = Runtime.build(config=self.config, providers={"c": self.fake}, db_path=":memory:")
        # 默认没有代码运行环境（不依赖本机是否装了沙箱）；工具测试中注入假沙箱
        self.rt.tools_sandbox = (None, "测试环境")
        self.sleeps: list[float] = []

        async def sleep(seconds: float) -> None:  # 媒体任务轮询：不真的等待
            self.sleeps.append(seconds)

        self.rt.media_hooks = {"sleep": sleep}
        self.events = []
        self.orc = Orchestrator(self.rt, on_event=lambda sid, e: self.events.append((sid, e)))

    def reply(self, model, messages):
        system = messages[0].content
        if "图片转写成" in system:
            return "图中文字：求 f(x)=x^3-3x 在 [-2, 2] 上的最值。坐标系中画有曲线。"
        if "逐字转写成文字稿" in system:
            return "说话人 1：请大家求这个函数的最大值和最小值。"
        if "规划员" in system:
            return planner(self.difficulty)(model, messages)
        if "汇总成一份结论" in system:
            return synthesis_reply(resolved=self.resolved)(model, messages)
        if "合并成一份完整成果" in system:
            return merge_reply("high" if self.resolved else "low")(model, messages)
        return default_reply(model, messages)

    def models_called(self, step_marker: str) -> list[str]:
        return [c.model for c in self.fake.calls if step_marker in c.messages[0].content]

    def tiers(self, model_ids):
        by = {m.id: m for m in self.config.models.models}
        return {by[i].tier for i in model_ids}


@pytest.fixture
def env() -> Env:
    return Env()
