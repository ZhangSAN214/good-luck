"""公寓楼：员工下班后住的地方。每位住户（同一厂商的模型算一位，昵称相同）按作息在各个房间活动；
正在办事的住户在办公室或会议室上班。

- 作息只看本地时间（小时、星期几）和住户的稳定编号，不随机：同一时刻打开看到的一样。
- 上班状态来自正在进行的会话的座位：**匿名开启且未揭晓的会话不暴露是谁**，只计入
  ``hidden_meetings``（会议室门上挂"匿名会议进行中"），这些住户照常按作息出现。
- 只读，不调用模型，不花钱。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from roundtable.core.config import AppConfig

# 公共房间：id → (名称, 楼层)。宿舍按住户数自动排在中间几层。
PUBLIC_ROOMS = {
    "rooftop": "天台",
    "office": "办公室",
    "meeting": "会议室",
    "canteen": "食堂",
    "lounge": "休息室",
    "gym": "健身房",
}
DORMS_PER_FLOOR = 3

# 活动：id → 显示文字。前端按 id 选动画。
ACTIVITIES = {
    "sleep": "睡觉",
    "eat": "吃饭",
    "idle": "待命",
    "work": "上班",
    "wait": "等你拍板",
    "exercise": "锻炼",
    "chat": "聊天",
    "read": "看书",
    "stargaze": "看星星",
    "game": "打游戏",
}

# 下班后的去处（按住户编号与日期轮换）
EVENING = (("gym", "exercise"), ("lounge", "chat"), ("rooftop", "stargaze"), ("lounge", "game"))
WEEKEND = (("lounge", "game"), ("rooftop", "stargaze"), ("gym", "exercise"), ("dorm", "read"))


@dataclass(frozen=True)
class Resident:
    id: str  # 厂商（配置里的 vendor）
    name: str  # 昵称
    model_ids: tuple[str, ...]
    role: str  # manager / staff
    dorm: str  # 宿舍房间 id
    room: str  # 现在所在的房间 id
    activity: str
    activity_label: str
    month_paid_usd: float = 0.0
    session_id: str | None = None  # 正在办的事（只在可公开时给出）


@dataclass
class Building:
    floors: list[list[dict[str, str]]]  # 从顶楼到一楼，每层若干房间 {id, name, kind}
    residents: list[Resident] = field(default_factory=list)
    hidden_meetings: int = 0  # 匿名且未揭晓的进行中会话数


def _stable(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:4], "big")


def schedule(resident_id: str, hour: int, weekday: int) -> tuple[str, str]:
    """(房间, 活动)。房间 "dorm" 表示自己的宿舍。weekday：0 = 周一。"""
    n = _stable(resident_id)
    late = n % 2  # 一半人晚一个小时起床 / 睡觉
    weekend = weekday >= 5
    if hour < 7 + late or hour >= 23 + late:
        return "dorm", "sleep"
    if hour < 8 + late:
        return "canteen", "eat"
    if hour in (12, 18):
        return "canteen", "eat"
    if not weekend and (8 + late <= hour < 12 or 13 <= hour < 18):
        return "office", "idle"
    pool = WEEKEND if weekend and hour < 18 else EVENING
    if hour >= 22:
        return "dorm", "read"
    return pool[(n + weekday + hour // 2) % len(pool)]


def _floors(dorms: list[str]) -> list[list[dict[str, str]]]:
    def room(rid: str) -> dict[str, str]:
        return {"id": rid, "name": PUBLIC_ROOMS[rid], "kind": rid}

    floors = [[room("rooftop")]]
    rooms = [{"id": d, "name": f"{i + 1:02d} 号房", "kind": "dorm"} for i, d in enumerate(dorms)]
    chunks = [rooms[i : i + DORMS_PER_FLOOR] for i in range(0, len(rooms), DORMS_PER_FLOOR)]
    floors += list(reversed(chunks))
    floors.append([room("office"), room("meeting")])
    floors.append([room("canteen"), room("lounge"), room("gym")])
    return floors


def building(
    config: AppConfig,
    *,
    hour: int,
    weekday: int,
    working: Iterable[Mapping[str, Any]] = (),
    month_usage: Mapping[str, Mapping[str, Any]] | None = None,
) -> Building:
    """working：进行中会话的座位 {session_id, model_id, status, hidden, workflow}；
    hidden 为真（匿名未揭晓）的只计数，不对应到住户。"""
    groups: dict[str, list[Any]] = {}
    for m in config.models.enabled:
        if m.seat and m.tier is not None:
            groups.setdefault(m.vendor, []).append(m)
    vendors = list(groups)
    dorms = [f"dorm{i + 1}" for i in range(len(vendors))]

    busy: dict[str, tuple[str, str, str]] = {}  # 模型 id → (房间, 活动, 会话)
    hidden = set()
    for seat in working:
        sid = str(seat["session_id"])
        if seat.get("hidden"):
            hidden.add(sid)
            continue
        activity = "wait" if seat.get("status") == "awaiting_confirmation" else "work"
        room = "office" if seat.get("workflow") in ("office", "solo") else "meeting"
        busy.setdefault(str(seat["model_id"]), (room, activity, sid))

    usage = month_usage or {}
    residents = []
    for vendor, dorm in zip(vendors, dorms, strict=True):
        models = groups[vendor]
        ids = tuple(m.id for m in models)
        found = next((busy[i] for i in ids if i in busy), None)
        if found:
            room, activity, sid = found
        else:
            room, activity = schedule(vendor, hour % 24, weekday % 7)
            sid = None
        residents.append(
            Resident(
                id=vendor,
                name=config.personas.nicknames.get(vendor, vendor),
                model_ids=ids,
                role="manager" if any(m.office_role == "manager" for m in models) else "staff",
                dorm=dorm,
                room=dorm if room == "dorm" else room,
                activity=activity,
                activity_label=ACTIVITIES[activity],
                month_paid_usd=round(
                    sum(float(usage.get(i, {}).get("cost_usd", 0.0)) for i in ids), 6
                ),
                session_id=sid,
            )
        )
    return Building(floors=_floors(dorms), residents=residents, hidden_meetings=len(hidden))
