"""P6 成长测试的共用材料：6 位成员的档案、阿一上一周的经历、假模型的成长回复。"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime

from roundtable.core.chat import ChatHub, dm_room

from ...orchestrator.conftest import Env

MONDAY = date(2026, 10, 19)  # 2026-W43
WEEK = "2026-W43"
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)  # 下一周的周一
TRAITS = ("外向", "较真", "好奇", "温和", "幽默")
SCORES = {
    "V1": (3, 4, 3, 4, 2),
    "V2": (5, 2, 5, 3, 5),
    "V3": (1, 5, 2, 2, 1),
    "V4": (4, 1, 4, 5, 3),
    "V5": (2, 3, 5, 1, 4),
    "V6": (5, 5, 1, 3, 3),
}
DIARIES = {
    "2026-10-19": "今天又去天文台看星星，越看越着迷。晚上和阿三聊了很久的黑洞。",
    "2026-10-21": "用户说我太较真了，我想了想，有些事确实可以放松一点。",
    "2026-10-23": "下雨没法看星星，在房间里翻了一晚上星图，还是很开心。",
}


def profile(member: str, scores=None) -> dict:
    traits = dict(zip(TRAITS, scores or SCORES[member], strict=True))
    return {
        "personality": {"五项": traits, "长处": ["耐心"], "性格": "原文整段"},
        "likes": {"最爱": ["红茶"]},
        "style": {"口头禅": [f"{member}的口头禅"]},
    }


def setup(env: Env, *, experiences: bool = True) -> None:
    hub = ChatHub(env.rt)
    store = env.rt
    from roundtable.core.member.profile import ProfileStore

    profiles = ProfileStore(store)
    for m in hub.everyone():
        profiles.change(m.id, profile(m.id), author="user", reason="测试")
    if not experiences:
        return
    repo = env.rt.repo
    for day, text in DIARIES.items():
        repo.add_memory("V1", "day", text, period=day)
    repo.add_memory_item("V1", "在天文台待了一晚上看流星", day="2026-10-21", domain="娱乐")
    repo.add_memory_item("V1", "交了一份关于极值的作业", day="2026-10-22", domain="工作")
    repo.add_memory("V1", "day", "上上周的日记，不该出现。", period="2026-10-12")
    hub.room(dm_room("V1"))
    repo.add_chat_message(
        dm_room("V1"), "user", "你最近好像特别爱看星星", created_at="2026-10-22T10:00:00+00:00"
    )
    hub.room(dm_room("V2"))
    repo.add_chat_message(
        dm_room("V2"), "user", "这句是对阿二说的", created_at="2026-10-22T11:00:00+00:00"
    )


def number_of(user_text: str, keyword: str) -> int:
    for line in user_text.split("<week>")[1].split("</week>")[0].splitlines():
        m = re.match(r"(\d+)\. ", line)
        if m and keyword in line:
            return int(m.group(1))
    raise AssertionError(f"经历里没有「{keyword}」")


GOOD = [
    ("score", "personality.五项.好奇", 1, "", "越看越着迷"),
    ("add", "likes.最爱", 0, "看星星", "在天文台待了一晚上看流星"),
]


def proposal(user_text: str, changes=GOOD) -> str:
    out = []
    for op, path, delta, value, quote in changes:
        out.append(
            {
                "op": op,
                "path": path,
                "delta": delta,
                "value": value,
                "evidence": [number_of(user_text, quote[:4])],
                "quote": quote,
                "why": "这周反复发生",
            }
        )
    return json.dumps({"changes": out, "skip": ""}, ensure_ascii=False)


def is_reflect(messages) -> bool:
    return "每周一次的成长反思" in messages[0].content


def is_review(messages) -> bool:
    return "当评审帮 TA 把关" in messages[0].content


def fake_growth(env: Env, *, reflect=None, score: int = 4) -> list:
    """把成长的两种提示词接到假模型上。reflect：依次返回的提议（函数或文本），用完后用 GOOD。"""
    queue = list(reflect or [])
    base = env.reply

    def reply(model, messages):
        if is_reflect(messages):
            r = queue.pop(0) if queue else proposal
            return r(messages[-1].content) if callable(r) else r
        if is_review(messages):
            block = messages[-1].content.split("<changes>")[1].split("</changes>")[0]
            n = len(re.findall(r"^\d+\. ", block, re.M))
            return json.dumps({"scores": [{"id": i, "score": score} for i in range(1, n + 1)]})
        return base(model, messages)

    env.fake._default = reply
    return queue


def growth_env(**growth) -> Env:
    """成长规则改几项的 Env。"""
    chat = Env().config.roundtable.chat
    rules = chat.engine.growth.model_copy(update=growth)
    engine = chat.engine.model_copy(update={"growth": rules})
    return Env(rt_update={"chat": chat.model_copy(update={"engine": engine})})
