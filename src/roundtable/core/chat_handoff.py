"""聊天里的活交给办公室（docs/CHAT.md「G14」，2026-11-01 用户批「按推荐进行」）。

办公室的模型是电脑，群聊里的成员是人：在群聊、私聊里被要求做模拟、报表、画图、写程序这类要动手的活时，
成员不在聊天里硬写一大段，而是说一两句，再写一个交办记号 ``[交办:要做的事]``。

- 记号不显示：这条消息下面出现一张「交给办公室」卡片，**你点了才开工**（会花钱，按办公室默认档位，
  超过单题确认门槛时照常要你确认）；点「先不用」卡片收起。
- 开工后按办公室模式办（经理分诊、派活、过目），题目只有「要做的事 + 你在聊天里的原话」，
  不带任何人设和聊天记忆（工作模式不受人设影响）。你在这段聊天里刚发的文件一起作为附件交过去。
- 做完或没办成时，以那位成员的名义把结果卡片发回原来的会话：标题、结论（可展开看全文，表格、
  Mermaid 图、公式照常显示）、生成的文件，以及「去办公室看」。
- 选项记号 ``[选项:甲|乙|丙]``：显示成几个按钮，你点哪个就替你发哪句（借 dsh-raw-html 的
  「按钮点了回一句话」的思路，用不着让模型写网页）。

全程序；只在生活群和私聊里用（工作群有自己的「交给办公室」按钮）。
"""

from __future__ import annotations

import json
import re
from typing import Any

HANDOFF_RE = re.compile(r"\[交办[:：]\s*([^\]\n]{2,300}?)\s*\]")
CHOICE_RE = re.compile(r"\[选项[:：]\s*([^\]\n]{1,200}?)\s*\]")
ORIGIN_KEY = "chat_handoff:"  # 设置表：办公室会话 id → 从哪个会话、哪位成员、哪条消息交过去的
PENDING, RUNNING, DONE, FAILED, DISMISSED = "pending", "running", "done", "failed", "dismissed"
LIFE_ROOMS = frozenset({"group", "dm"})
REQUEST_CHARS = 400  # 带上你在聊天里的原话最多多少字
RESULT_CHARS = 6000  # 结果卡片里最多放多少字的结论（全文去办公室看）
CHOICE_MAX = 4
CHOICE_CHARS = 24


def extract(text: str) -> tuple[str, str | None]:
    """从一条成员消息里取出交办记号：(去掉记号后的话, 要做的事)。没有记号时第二项为 None；
    一条里有几个只取第一个。"""
    m = HANDOFF_RE.search(text or "")
    if m is None:
        return text, None
    task = " ".join(m.group(1).split())
    rest = HANDOFF_RE.sub("", text)
    rest = re.sub(r"[ \t]+\n", "\n", rest).strip()
    return rest, task or None


def choices(text: str) -> tuple[str, list[str]]:
    """取出选项记号：(去掉记号后的话, [选项])。最多 CHOICE_MAX 个，每个最多 CHOICE_CHARS 字。"""
    m = CHOICE_RE.search(text or "")
    if m is None:
        return text, []
    items = []
    for raw in re.split(r"[|｜/／]", m.group(1)):
        item = raw.strip()[:CHOICE_CHARS]
        if item and item not in items:
            items.append(item)
    rest = CHOICE_RE.sub("", text).strip()
    return rest, items[:CHOICE_MAX] if len(items) >= 2 else []


def pending(task: str) -> dict[str, Any]:
    return {"task": task, "state": PENDING}


def question(task: str, request: str) -> str:
    """交给办公室的题目：要做的事在前，你在聊天里的原话放在标签里附在后面（只是资料）。"""
    req = " ".join((request or "").split())[:REQUEST_CHARS]
    if not req or req == task:
        return task
    return f"{task}\n\n（以下是提出这件事时的原话，供参考）\n<request>\n{req}\n</request>"


def remember(rt: Any, sid: str, *, room: str, member: str, message_id: int) -> None:
    rt.repo.set_setting(
        ORIGIN_KEY + sid, json.dumps({"room": room, "member": member, "message_id": message_id})
    )


def origin(rt: Any, sid: str) -> dict[str, Any] | None:
    raw = rt.repo.setting(ORIGIN_KEY + sid)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) and data.get("room") and data.get("member") else None


def forget(rt: Any, sid: str) -> None:
    rt.repo.set_setting(ORIGIN_KEY + sid, "")


def mark(rt: Any, message_id: int, **fields: Any) -> dict[str, Any] | None:
    """改一条消息上交办卡片的状态（state、session_id……）；返回新的 meta。"""
    row = rt.repo.chat_message(message_id)
    if row is None:
        return None
    meta = dict(row.get("meta") or {})
    card = dict(meta.get("handoff") or {})
    if not card:
        return None
    card.update(fields)
    meta["handoff"] = card
    rt.repo.set_chat_message_meta(message_id, meta)
    return meta


def result_card(
    *, sid: str, title: str, ok: bool, text: str, files: list[dict[str, Any]], cost: float
) -> dict[str, Any]:
    body = (text or "").strip()
    cut = len(body) > RESULT_CHARS
    return {
        "session_id": sid,
        "title": title,
        "ok": ok,
        "text": body[:RESULT_CHARS].rstrip() + ("…" if cut else ""),
        "cut": cut,
        "files": files[:12],
        "cost_usd": round(float(cost or 0.0), 4),
    }
