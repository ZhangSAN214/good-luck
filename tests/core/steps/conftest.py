"""单桌测试环境：合成的模型池 + 按模型编排回复的 Fake provider + 内存数据库。"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable

import pytest

from roundtable.core.allocation import IdentityScrubber
from roundtable.core.config import load_config
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers import ChannelRouter, FakeProvider, Message
from roundtable.core.routing import Question
from roundtable.core.steps import TableContext
from roundtable.core.storage import Repository, connect

from ..routing.conftest import app_config

REPO_CONFIG = load_config()
QUESTION = "求函数 f(x)=x^3-3x 在区间 [-2, 2] 上的最大值与最小值。"
MEMBERS = {"甲": "b1", "乙": "b2", "丙": "b3"}
COORDINATOR = "f1"
LABEL = re.compile(r'<answer code="组员(.)"[^>]*>')
# 合格答案需要有实质内容（防偷懒检查有字数下限），Fake 的回复因此带上完整推导
REASONING = (
    "推导：f'(x)=3x^2-3，令 f'(x)=0 得驻点 x=-1 和 x=1。区间端点为 x=-2 和 x=2。"
    "计算函数值：f(-2)=-8+6=-2，f(-1)=-1+3=2，f(1)=1-3=-2，f(2)=8-6=2。"
    "比较驻点与端点处的函数值，最大值为 2（在 x=-1 与 x=2 处取得），"
    "最小值为 -2（在 x=-2 与 x=1 处取得）。结论：最大值 2，最小值 -2。"
)
REVISED = (
    "修订说明：按审阅意见补充了单调性分析。f'(x)=3(x-1)(x+1)，在 (-2,-1) 上大于零，"
    "在 (-1,1) 上小于零，在 (1,2) 上大于零，所以函数先增后减再增。"
    "极大值 f(-1)=2，极小值 f(1)=-2，再与端点值 f(-2)=-2、f(2)=2 比较，"
    "得到区间上的最大值为 2、最小值为 -2，与原结论一致，但论证更完整。"
)


def user_text(messages) -> str:
    """模型看到的全部用户内容（重做时题目和材料在前面的轮次里）。"""
    return "\n".join(m.content for m in messages if m.role == "user")


def labels_in(messages) -> list[str]:
    return LABEL.findall(user_text(messages))


def good_review(model: str, messages: list[Message]) -> str:
    """对看到的每一份答案给出一条合格的评审。"""
    reviews = [
        {
            "target": f"组员{code}",
            "verdict": "partially_correct",
            "issues": [
                {
                    "location": "第 2 步",
                    "problem": f"{model} 指出的问题",
                    "suggestion": "改正",
                    "severity": "major",
                }
            ],
            "checked": "",
            "strengths": "思路清楚",
        }
        for code in labels_in(messages)
    ]
    return json.dumps({"reviews": reviews}, ensure_ascii=False)


REVIEW_FROM = re.compile(r'<review from="组员(.)"')


def revision_reply(model: str, messages: list[Message]) -> str:
    """逐条回应收到的评审：每位评审者的问题 1 都采纳。"""
    received = REVIEW_FROM.findall(user_text(messages)) if messages else []
    lines = [f"- 组员{c} · 问题 1：采纳 —— 指出得对" for c in received]
    responses = "\n".join(lines) or "采纳。"
    return f"## 修订后的答案\n{model} 的修订稿。{REVISED}\n\n## 对审阅意见的回应\n{responses}"


def synthesis_reply(resolved=True, confidence="high") -> Callable:
    def reply(model: str, messages: list[Message]) -> str:
        codes = labels_in(messages)
        return json.dumps(
            {
                "consensus": ["最大值 2"],
                "disagreements": [
                    {
                        "point": "最小值",
                        "positions": [{"members": [f"组员{codes[0]}", "组员戊"], "view": "-2"}],
                        "assessment": "依据端点",
                        "resolved": resolved,
                    }
                ],
                "final_answer": "最大值 2，最小值 -2",
                "adopted_from": [
                    {"point": "端点与驻点比较", "members": [f"组员{c}" for c in codes]},
                    {"point": "单调性分析", "members": [f"组员{codes[0]}", "组员癸"]},
                ],
                "open_questions": [],
                "confidence": confidence,
            },
            ensure_ascii=False,
        )

    return reply


SUBTASK_ID = re.compile(r'<subtask id="(T\d+)">')
VOLUNTEER_CODE = re.compile(r'<volunteer code="组员(.)">')
WORK_ID = re.compile(r'<work id="(W\d+)"')
MERGE_WORK = re.compile(r'<work id="W\d+" subtask="(T\d+)" from="组员(.)"')


def decompose_reply(n: int = 2, depends: bool = False) -> Callable:
    def reply(model: str, messages: list[Message]) -> str:
        subtasks = [
            {
                "id": f"T{i}",
                "title": f"第 {i} 部分",
                "requirements": f"完成第 {i} 部分的推导",
                "acceptance": "推理正确、结论明确",
                "tags": ["math"],
                "depends_on": ["T1"] if depends and i > 1 else [],
            }
            for i in range(1, n + 1)
        ]
        return json.dumps({"subtasks": subtasks, "notes": "按步骤拆分"}, ensure_ascii=False)

    return reply


def volunteer_reply(model: str, messages: list[Message]) -> str:
    ids = SUBTASK_ID.findall(user_text(messages))
    prefs = [
        {
            "subtask": sid,
            "stance": "want" if i == 0 else "can",
            "reason": "这一部分主要是求导与比较函数值，我擅长这类推导",
        }
        for i, sid in enumerate(ids)
    ]
    return json.dumps(
        {"strengths": "擅长微积分推导与数值核对，能写出完整步骤", "preferences": prefs},
        ensure_ascii=False,
    )


def assign_reply(model: str, messages: list[Message]) -> str:
    """轮流分配：每人至少一块，每块至少一人。"""
    text = user_text(messages)
    ids, codes = SUBTASK_ID.findall(text), VOLUNTEER_CODE.findall(text)
    owners: dict[str, list[str]] = {sid: [] for sid in ids}
    for i in range(max(len(ids), len(codes))):
        owners[ids[i % len(ids)]].append(f"组员{codes[i % len(codes)]}")
    return json.dumps(
        {
            "assignments": [{"subtask": k, "members": sorted(set(v))} for k, v in owners.items()],
            "rationale": "按自荐轮流分配",
        },
        ensure_ascii=False,
    )


def cross_review_reply(model: str, messages: list[Message]) -> str:
    reviews = [
        {
            "target": item,
            "verdict": "partially_correct",
            "issues": [
                {
                    "location": "第 2 步",
                    "problem": f"{model} 指出的问题",
                    "suggestion": "改正",
                    "severity": "minor",
                }
            ],
            "checked": "",
            "strengths": "步骤清楚",
        }
        for item in WORK_ID.findall(user_text(messages))
    ]
    return json.dumps({"reviews": reviews}, ensure_ascii=False)


def merge_reply(confidence: str = "high", legacy: bool = False) -> Callable:
    """merge/v2 的格式：Markdown 正文 + 末尾 JSON 合并说明（legacy=True 时给出 v1 的整段 JSON）。"""

    def reply(model: str, messages: list[Message]) -> str:
        found = MERGE_WORK.findall(user_text(messages))
        subtasks: dict[str, list[dict]] = {}
        for sid, code in found:
            subtasks.setdefault(sid, []).append(
                {"member": f"组员{code}", "level": "full", "reason": "推导完整"}
            )
        notes = {
            "subtasks": [{"subtask": k, "adopted": v} for k, v in subtasks.items()],
            "gaps": [],
            "open_questions": [],
            "confidence": confidence,
        }
        result = "完整成果：最大值 2，最小值 -2"
        if legacy:
            return json.dumps({"result": result, **notes}, ensure_ascii=False)
        body = json.dumps(notes, ensure_ascii=False, indent=2)
        return f"## 完整成果\n{result}\n\n## 合并说明\n```json\n{body}\n```"

    return reply


def default_reply(model: str, messages: list[Message]) -> str:
    """按提示词判断步骤，给出合格回复。"""
    system = messages[0].content
    if "把任务拆成子任务" in system and "学习小组的统筹" in system:
        return decompose_reply()(model, messages)
    if "现在请你自荐" in system:
        return volunteer_reply(model, messages)
    if "负责把子任务分配给组员" in system:
        return assign_reply(model, messages)
    if "请你审查分给你的几份成果" in system:
        return cross_review_reply(model, messages)
    if "负责的子任务已经由其他组员审查" in system:
        return revision_reply(model, messages)
    if "合并成一份完整成果" in system:
        return merge_reply()(model, messages)
    if "审阅每一份答案" in system:
        return good_review(model, messages)
    if "根据审阅意见修订" in system:
        return revision_reply(model, messages)
    if "学习小组的统筹" in system:
        return synthesis_reply()(model, messages)
    return f"{model} 的答案：最大值 2，最小值 -2。{REASONING}"


class Table:
    def __init__(self, seed: int = 0, members=None, coordinator=COORDINATOR):
        self.config = app_config()
        self.fake = FakeProvider("c", default=default_reply)
        policy = self.config.roundtable.request.model_copy(update={"backoff_s": 0})
        self.router = ChannelRouter(self.config.models, {"c": self.fake}, policy=policy)
        self.repo = Repository(connect())
        self.session = self.repo.create_session(QUESTION, seed=seed)
        self.events = []
        self.ctx = TableContext(
            session_id=self.session,
            table_no=0,
            question=Question(QUESTION),
            members=dict(members or MEMBERS),
            coordinator=coordinator,
            config=self.config,
            router=self.router,
            prompts=PromptLibrary(),
            repo=self.repo,
            scrubber=IdentityScrubber.from_config(REPO_CONFIG.models),
            rng=random.Random(seed),
            on_event=self.events.append,
        )

    def calls_for(self, model: str):
        return [c for c in self.fake.calls if c.model == model]

    def prompts_for(self, model: str) -> list[str]:
        return [c.messages[-1].content for c in self.calls_for(model)]


@pytest.fixture
def table() -> Table:
    return Table()
