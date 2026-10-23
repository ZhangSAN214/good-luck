from __future__ import annotations

import json
from pathlib import Path

import pytest

from roundtable.core.orchestrator import Orchestrator
from roundtable.core.providers import FakeProvider
from roundtable.core.runtime import Runtime

from ..routing.conftest import app_config
from ..steps.conftest import default_reply, merge_reply, synthesis_reply

NO_MEMBERS = Path(__file__).parent / "_no_members"
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
        self,
        difficulty="medium",
        resolved=True,
        pool=None,
        with_media=False,
        pipeline=False,
        rt_update=None,
        members_dir=None,
        **routing_overrides,
    ):
        self.difficulty = difficulty
        self.resolved = resolved
        self.office_mode = (
            "single"  # 办公室经理分诊时建议的办事方式（meeting / project：点名前两位）
        )
        self.office_review = "pass"  # 项目分工：复核经理验收的结论
        self.office_check = "pass"  # 单人办：经理过目的结论（revise 时退回一次）
        self.config = app_config(
            **({"pool": pool} if pool else {}),
            with_media=with_media,
            pipeline=pipeline,
            rt_update=rt_update,
            **routing_overrides,
        )
        self.fake = FakeProvider("c", default=self.reply)
        policy = self.config.roundtable.request.model_copy(update={"backoff_s": 0})
        rt_cfg = self.config.roundtable.model_copy(update={"request": policy})
        self.config = self.config.model_copy(update={"roundtable": rt_cfg})
        # 默认指向不存在的目录：测试不读用户本机的形象素材
        self.rt = Runtime.build(
            config=self.config,
            providers={"c": self.fake},
            db_path=":memory:",
            members_dir=members_dir or NO_MEMBERS,
        )
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
        if "逐条验收" in system and "合并之前" in system:  # 统筹验收：默认全部通过
            return json.dumps({"items": []})
        if "合并成一份完整成果" in system:
            return merge_reply("high" if self.resolved else "low")(model, messages)
        if "办公室的经理" in system and "派给合适的员工" in system:  # 办公室：派给名单第一位
            staff = messages[-1].content.split("<staff>")[1].split("</staff>")[0]
            names = [x.removeprefix("- ").split("｜")[0] for x in staff.strip().splitlines()]
            return json.dumps(
                {
                    "mode": self.office_mode,
                    "assignee": names[0],
                    "attendees": names[:2] if self.office_mode != "single" else [],
                    "brief": "写清推导",
                },
                ensure_ascii=False,
            )
        if "复核经理" in system and "最终验收" in system:
            issues = ["补上单调区间的说明"] if self.office_review == "revise" else []
            return json.dumps(
                {"verdict": self.office_review, "issues": issues, "note": "核对了结论"},
                ensure_ascii=False,
            )
        if "主办了一个项目" in system and "验收意见" in system:
            return "修改后的项目成果：最大值 2，最小值 -2；已补上单调区间的说明。"
        if "办公室的经理" in system and "拍板" in system and "会议纪要" in system:
            return "## 结论\n最大值 2，最小值 -2。\n\n## 会议纪要\n两位意见一致。"
        if "你在一个群聊里" in system:  # 群聊：回一句短话
            return f"收到，我是 {model} 的模型，我们聊聊。"
        if "办公室的经理" in system and "过目" in system:
            issues = ["补上端点的函数值"] if self.office_check == "revise" else []
            return json.dumps(
                {"verdict": self.office_check, "issues": issues, "note": "核对了结论"},
                ensure_ascii=False,
            )
        return default_reply(model, messages)

    def models_called(self, step_marker: str) -> list[str]:
        return [c.model for c in self.fake.calls if step_marker in c.messages[0].content]

    def tiers(self, model_ids):
        by = {m.id: m for m in self.config.models.models}
        return {by[i].tier for i in model_ids}


@pytest.fixture
def env() -> Env:
    return Env()
