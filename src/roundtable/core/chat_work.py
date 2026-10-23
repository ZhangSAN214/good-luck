"""工作群：把办公室里真实发生的事写成工作群里的系统消息（交办、派活、开会、等你拍板、交付）。

- 只读会话记录和编排事件，不调用模型、不花钱。
- 匿名开启且未揭晓的会话不写任何人的名字（只说「经理」「一位员工」「N 位员工」）。
- 每条消息的 meta 带 ``session_id``，网页上点「查看」回到办公室里的这件事。
"""

from __future__ import annotations

from typing import Any

from roundtable.core.allocation.names import coordinator_name
from roundtable.core.chat import SYSTEM, WORK

# 会触发工作群消息的编排事件
WORK_EVENTS = frozenset({"office_assigned", "checkpoint", "completed", "failed"})
TITLE_CHARS = 40


def short_title(question: str) -> str:
    text = " ".join(question.split())
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS].rstrip() + "…"


def _hidden(row: dict[str, Any]) -> bool:
    return bool(row["anonymous"]) and not row["revealed_at"]


def _manager(rt: Any, sid: str, row: dict[str, Any]) -> str:
    if _hidden(row):
        return "经理"
    tables = rt.repo.tables(sid)
    if not tables or not tables[0].get("coordinator"):
        return "经理"
    return coordinator_name(tables[0]["coordinator"], rt.config, office=True)


def _people(names: list[str], hidden: bool) -> str:
    return f"{len(names)} 位员工" if hidden else "、".join(names)


def office_message(rt: Any, sid: str, type_: str, data: dict[str, Any], code: str | None) -> str:
    """一条编排事件对应的工作群消息文字；不需要发时返回空串。"""
    if type_ not in WORK_EVENTS:
        return ""
    row = rt.repo.session_row(sid)
    if row["workflow"] != "office":
        return ""
    title = f"「{short_title(row['question'])}」"
    hidden = _hidden(row)
    if type_ == "office_assigned":
        manager = _manager(rt, sid, row)
        mode = data.get("mode")
        attendees = list(data.get("attendees") or [])
        if mode == "meeting":
            return f"{manager}为{title}召集开会：{_people(attendees, hidden)}参加。"
        if mode == "project":
            return f"{manager}决定{title}做项目分工，项目组：{_people(attendees, hidden)}。"
        who = "一位员工" if hidden or not code else code
        return f"{manager}把{title}派给了{who}。"
    if type_ == "checkpoint":
        return f"{title}在等你拍板，去办公室看看。"
    if type_ == "completed":
        cost = rt.repo.session_cost(sid)
        return f"{title}交付了，这件事花了 ${cost:.4f}。"
    if type_ == "failed":
        return f"{title}没办成，去办公室看看原因。"
    return ""


def post_office_event(
    rt: Any, sid: str, type_: str, data: dict[str, Any], code: str | None
) -> dict[str, Any] | None:
    text = office_message(rt, sid, type_, data, code)
    if not text:
        return None
    return rt.repo.add_chat_message(WORK, SYSTEM, text, meta={"session_id": sid, "event": type_})


def post_handoff(rt: Any, sid: str, question: str) -> dict[str, Any]:
    """老板在工作群里交办了一件事（用户自己的消息，带上办公室会话 id）。"""
    return rt.repo.add_chat_message(
        WORK, "user", question, meta={"session_id": sid, "event": "handoff"}
    )
