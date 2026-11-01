"""生活状态的存取（每位成员一份 JSON，存 ``app_settings`` 的 ``member_life:<成员>``，不加迁移）。

只有生活引擎（本包）写；用户可以一键清空（``clear``）。内容：
- ``pending``：排好的日程（今天和以后几天要发生的每一步，含长线剧情的后续）和最近做过的事；
- ``planned``：哪几天已经挑过事（每天只挑一次，可复现）；
- ``last`` / ``group_last``：每件事、每个分组最近一次开始的日期（冷却用）；
- ``talents``：各领域的擅长阶段；``p6``：等成长插件（P6）写进档案的擅长变化。
"""

from __future__ import annotations

import copy
import json
from collections import defaultdict
from typing import Any

from roundtable.core.member.profile import ProfileStore
from roundtable.core.office.apartment import PUBLIC_ROOMS

KEY = "member_life:"
ROSTER_KEY = "member_life_roster"
TALENT_KEY = "擅长"  # 档案 likes.擅长（由 P6 写）；算种子时不算它，免得自己证明自己


def empty() -> dict[str, Any]:
    return {"pending": [], "planned": [], "last": {}, "group_last": {}, "talents": {}, "p6": []}


def load(rt: Any, member: str) -> dict[str, Any]:
    raw = rt.repo.setting(KEY + member)
    if not raw:
        return empty()
    try:
        data = json.loads(raw)
    except ValueError:
        return empty()
    return empty() | (data if isinstance(data, dict) else {})


def save(rt: Any, member: str, data: dict[str, Any]) -> None:
    rt.repo.set_setting(KEY + member, json.dumps(data, ensure_ascii=False))


def clear(rt: Any, member: str) -> None:
    rt.repo.set_setting(KEY + member, "")


def place_label(place: str) -> str:
    if not place or place.startswith("dorm"):
        return "自己的宿舍"
    if place == "out":
        return "外面"
    return f"公寓楼{PUBLIC_ROOMS.get(place, place)}"


def fill(text: str, **names: str) -> str:
    """把 {name} {other} {domain} {new} {gone} 换成真名；没给的占位符换成空。"""
    return text.format_map(defaultdict(str, names))


def profile_text(rt: Any, member: str) -> str:
    """档案全文（算爱好和擅长的种子用），不含 P6 写的 likes.擅长。"""
    data = copy.deepcopy(ProfileStore(rt).current(member).data)
    likes = data.get("likes")
    if isinstance(likes, dict):
        likes.pop(TALENT_KEY, None)
    return json.dumps(data, ensure_ascii=False)


def hits(text: str, words: list[str], cap: int = 3) -> int:
    """档案里提到这些词的次数（最多算 cap 次：档案长不等于什么都爱）。"""
    return min(cap, sum(text.count(w) for w in words if w))
