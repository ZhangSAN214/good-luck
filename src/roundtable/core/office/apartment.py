"""公寓楼：员工下班后住的地方。每位住户（同一厂商的模型算一位，昵称相同）按作息在各个房间活动；
正在办事的住户在办公室或会议室上班。

- 作息只看本地时间（小时、星期几）和住户的稳定编号，不随机：同一时刻打开看到的一样。
- 生活系统这一小时已经记下的去处（大脑按驱力、目标、习惯自己挑的，``life_spots``）优先于作息表，
  和生活群、朋友圈里说的对得上；还没记下（这一小时的生活还没推进、关着生活）才按作息表。
- 上班状态来自正在进行的会话的座位：**匿名开启且未揭晓的会话不暴露是谁**，只计入
  ``hidden_meetings``（会议室门上挂"匿名会议进行中"），这些住户照常按作息出现。
- 只读，不调用模型，不花钱。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Collection, Iterable, Mapping
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

# 会话 → 桌号 → 在会议室的代号（None 为整桌）；经理（统筹）随桌进会议室
Meetings = Mapping[str, Mapping[int, Collection[str] | None]]

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

# 白天上天台不看星星：同一个活动白天叫「晒太阳」（动画、聚在一起等规则照旧按活动 id）
DAYTIME_LABELS = {"stargaze": "晒太阳"}
DAY_HOURS = range(6, 18)


def activity_label(act: str, hour: int) -> str:
    """活动的显示文字（按钟点）：白天的「看星星」说成「晒太阳」；不认识的活动照原样。"""
    if hour % 24 in DAY_HOURS and act in DAYTIME_LABELS:
        return DAYTIME_LABELS[act]
    return ACTIVITIES.get(act, act)


# 下班后的去处（按住户编号与日期轮换）
EVENING = (("gym", "exercise"), ("lounge", "chat"), ("rooftop", "stargaze"), ("lounge", "game"))
WEEKEND = (("lounge", "game"), ("rooftop", "stargaze"), ("gym", "exercise"), ("dorm", "read"))


# 生活事件场景里的「地点：公寓楼休息室（聊天）」：括号里是在做的事
_SCENE_ACT = re.compile(r"^地点：[^\n]*（([^\n（）]+)）\s*$", re.M)
_ACT_IDS = {label: aid for aid, label in ACTIVITIES.items()} | {
    label: aid for aid, label in DAYTIME_LABELS.items()
}

# 住户 → (房间, 活动 id, 显示文字)
Spots = Mapping[str, tuple[str, str, str]]


def life_spots(events: Iterable[Mapping[str, Any]]) -> dict[str, tuple[str, str, str]]:
    """生活事件（``life_events`` 行，按时间先后）→ 每位住户最后记下的去处。

    私聊（pair，没有地点）不算；地点不是公寓房间的跳过。活动不在 ``ACTIVITIES`` 里的
    （大脑写的「琢磨「某事」」这类）照原样显示，动画在宿舍按看书、在别处按待命。"""
    out: dict[str, tuple[str, str, str]] = {}
    for e in events:
        if e.get("kind") not in ("gather", "solo"):
            continue
        place = str(e.get("place") or "")
        room = "dorm" if place == "dorm" or place.startswith("dorm:") else place
        if room != "dorm" and room not in PUBLIC_ROOMS:
            continue
        found = _SCENE_ACT.search(str(e.get("topic") or ""))
        label = found.group(1).strip() if found else ""
        aid = _ACT_IDS.get(label) or ("read" if room == "dorm" else "idle")
        if aid == "sleep":
            continue
        for member in e.get("members") or ():
            out[str(member)] = (room, aid, label or ACTIVITIES[aid])
    return out


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


def seat_room(seat: Mapping[str, Any], meetings: Meetings) -> str:
    """进行中会话的一个座位在哪个房间：讨论类在会议室，单人和办公室模式在办公室；
    办公室模式开会的参会者与主持的经理、项目分工的项目组在会议室。"""
    workflow = seat.get("workflow")
    if workflow == "solo":
        return "office"
    if workflow != "office":
        return "meeting"
    tables = meetings.get(str(seat["session_id"]), {})
    table_no = seat.get("table_no")
    if table_no not in tables:
        return "office"
    codes = tables[table_no]
    if codes is None or seat.get("role") == "coordinator" or seat.get("code") in codes:
        return "meeting"
    return "office"


def building(
    config: AppConfig,
    *,
    hour: int,
    weekday: int,
    working: Iterable[Mapping[str, Any]] = (),
    month_usage: Mapping[str, Mapping[str, Any]] | None = None,
    meetings: Meetings | None = None,
    life: Spots | None = None,
) -> Building:
    """working：进行中会话的座位 {session_id, model_id, status, hidden, workflow,
    table_no, role, code}；hidden 为真（匿名未揭晓）的只计数，不对应到住户。

    meetings：办公室模式里在会议室的桌子，会话 → 桌号 → 参会员工的代号（None 为整桌，
    如项目组）。参会员工和主持的经理在会议室；其余办公室模式的座位在办公室。

    life：生活系统这一小时记下的去处（``life_spots``）；上班的照样在办公室 / 会议室。"""
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
        room = seat_room(seat, meetings or {})
        model_id = str(seat["model_id"])
        prev = busy.get(model_id)
        # 同一会话里既在办公室又在会议室的（经理主持会议、项目组成员），算在会议室
        if prev is None or (prev[2] == sid and room == "meeting"):
            busy[model_id] = (room, activity, sid)

    usage = month_usage or {}
    residents = []
    for vendor, dorm in zip(vendors, dorms, strict=True):
        models = groups[vendor]
        ids = tuple(m.id for m in models)
        hits = [busy[i] for i in ids if i in busy]
        found = next((h for h in hits if h[0] == "meeting" and h[2] == hits[0][2]), None)
        found = found or (hits[0] if hits else None)
        label = None
        spot = (life or {}).get(vendor)
        if found:
            room, activity, sid = found
        elif spot:
            room, activity, label = spot
            sid = None
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
                activity_label=label or activity_label(activity, hour),
                month_paid_usd=round(
                    sum(float(usage.get(i, {}).get("cost_usd", 0.0)) for i in ids), 6
                ),
                session_id=sid,
            )
        )
    return Building(floors=_floors(dorms), residents=residents, hidden_meetings=len(hidden))
