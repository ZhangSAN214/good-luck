"""习惯计数（M17）和技能熟练度（M18）：全由程序从**全部经历**累积，不调用模型。

- **习惯**：读事件总线存下的全部事件（``member_events`` 一直保留），按成员累加计数——
  生活里做什么（聚在一起 / 两人聊天 / 一个人待着）、常去哪、在哪个时段说话、干完几件活、
  看过听过什么。每项记总次数、出现过几周、最后一次是哪周。开机第一次从头算（全量经历），
  之后只读游标以后的新事件。次数 ≥ ``habits.min_count`` 且出现在 ≥ ``habits.min_weeks``
  个不同的周里，才算「习惯」（偶尔一次不算）。
- **技能熟练度**：按记忆件的领域（学术 / 日常 / 工作 / 娱乐 / 社交 / 身心）把全部经历的
  件数和合并次数加起来当经验，等级 = log2(1 + 经验)（最多 10 级），越往上越难升。
- 数据存在成长插件自己的 JSON 里（``timeline.py`` 负责读写），所有成员同一套规则。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, tzinfo
from typing import Any

from roundtable.core.member.growth.week import week_id

KINDS = {"gather": "和大家聚在一起", "pair": "和人私下聊天", "solo": "一个人待着"}
SLOTS = ((5, "早上"), (11, "中午"), (14, "下午"), (18, "晚上"), (23, "深夜"))
LEVELS = ((9, "精通"), (7, "拿手"), (5, "熟练"), (3, "熟悉"), (1, "入门"), (0, "没怎么碰过"))
MAX_LEVEL = 10


def slot(hour: int) -> str:
    """几点 → 时段（23 点到 5 点算深夜）。"""
    name = "深夜"
    for start, label in SLOTS:
        if hour >= start:
            name = label
    return name


def keys_of(
    event: dict[str, Any], tz: tzinfo | None, place_name: Callable[[str], str]
) -> list[str]:
    """一个事件算进哪几项习惯（「种类:名字」）。"""
    kind, data = event["kind"], event["data"] or {}
    at = datetime.fromisoformat(event["at"]).astimezone(tz)
    if kind == "life.event":
        out = []
        if data.get("kind") in KINDS:
            out.append(f"做什么:{KINDS[data['kind']]}")
        if data.get("place") and data.get("kind") != "pair":
            out.append(f"常去:{place_name(str(data['place']))}")
        return out
    if kind == "chat.message" and not data.get("from_user"):
        return [f"说话:{slot(at.hour)}"]
    if kind == "work.done":
        return ["干活:在办公室干活"]
    if kind.startswith("perceive."):
        return [f"看过听过:{kind.removeprefix('perceive.')}"]
    return []


def count(
    blob: dict[str, Any],
    events: list[dict[str, Any]],
    tz: tzinfo | None,
    place_name: Callable[[str], str] = str,
) -> int:
    """把这位成员的新事件累加进 blob["counts"]（按时间先后）。返回算进去的事件数。"""
    counts = blob.setdefault("counts", {})
    n = 0
    for e in events:
        keys = keys_of(e, tz, place_name)
        if not keys:
            continue
        week = week_id(datetime.fromisoformat(e["at"]).astimezone(tz).date())
        for key in keys:
            c = counts.setdefault(key, {"n": 0, "weeks": 0, "last": None})
            c["n"] += 1
            if c["last"] != week:
                c["weeks"] += 1
                c["last"] = week
        n += 1
    return n


def label(key: str) -> str:
    kind, _, name = key.partition(":")
    if kind == "说话":
        return f"常在{name}说话"
    if kind == "常去":
        return f"常去{name}"
    if kind == "看过听过":
        return f"常看 / 听 {name}"
    return name


def habits(blob: dict[str, Any], rules: Any) -> list[dict[str, Any]]:
    """够得上「习惯」的几项（次数多的在前）。"""
    out = [
        {"key": k, "label": label(k), "n": c["n"], "weeks": c["weeks"], "last": c["last"]}
        for k, c in (blob.get("counts") or {}).items()
        if c["n"] >= rules.min_count and c["weeks"] >= rules.min_weeks
    ]
    out.sort(key=lambda h: (-h["n"], h["key"]))
    return out[: rules.top]


def level(xp: float) -> int:
    return min(MAX_LEVEL, int(math.log2(1 + max(xp, 0))))


def level_name(lv: int) -> str:
    return next(name for floor, name in LEVELS if lv >= floor)


def skills(stats: list[dict[str, Any]], domains: list[str]) -> list[dict[str, Any]]:
    """每个领域的经验（件数 + 合并次数）和等级。stats：``memory_item_stats`` 的结果。"""
    by = {s["domain"]: (s.get("items") or 0) + (s.get("hits") or 0) for s in stats}
    out = []
    for d in domains:
        xp = int(by.get(d, 0))
        lv = level(xp)
        nxt = 2 ** (lv + 1) - 1 if lv < MAX_LEVEL else None
        out.append({"domain": d, "xp": xp, "level": lv, "name": level_name(lv), "next": nxt})
    return out


def text(hs: list[dict[str, Any]], sk: list[dict[str, Any]], limit: int = 160) -> str:
    """给每周反思用的一段：习惯（次数、跨几周）和拿得出手的领域。"""
    parts = []
    if hs:
        parts.append(
            "习惯：" + "、".join(f"{h['label']}（{h['n']} 次，{h['weeks']} 周）" for h in hs)
        )
    good = [s for s in sorted(sk, key=lambda s: -s["xp"]) if s["level"] >= 3][:3]
    if good:
        parts.append(
            "熟练度：" + "、".join(f"{s['domain']} {s['level']} 级（{s['name']}）" for s in good)
        )
    out = "；".join(parts)
    return out if len(out) <= limit else out[: limit - 1] + "…"
