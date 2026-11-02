"""旅行打卡与 B 站记录的存取（``app_settings`` 里的 JSON，不加迁移；只存本机）。

- ``member_checkin``：打卡记录（判定卡、过没过、旅行日记、配图），最多留 ``KEEP`` 条；
- ``member_bili``：看过的视频（标题、看到的记录、取的几帧）和发件箱（私信、动态）；
- ``member_bili_auto``：B 站一键总开关（``off`` 为关，默认开）；
- ``member_checkin_photo``：打卡配图开关（``on`` 为开，默认关：真出图前先问用户）。
"""

from __future__ import annotations

import json
from typing import Any

CHECKIN_KEY = "member_checkin"
BILI_KEY = "member_bili"
AUTO_KEY = "member_bili_auto"
PHOTO_KEY = "member_checkin_photo"
KEEP = 120


def _load(rt: Any, key: str, empty: Any) -> Any:
    raw = rt.repo.setting(key)
    if not raw:
        return empty
    try:
        data = json.loads(raw)
    except ValueError:
        return empty
    return data if isinstance(data, type(empty)) else empty


def checkins(rt: Any) -> list[dict[str, Any]]:
    return _load(rt, CHECKIN_KEY, [])


def save_checkins(rt: Any, rows: list[dict[str, Any]]) -> None:
    rt.repo.set_setting(CHECKIN_KEY, json.dumps(rows[-KEEP:], ensure_ascii=False))


def bili(rt: Any) -> dict[str, Any]:
    data = _load(rt, BILI_KEY, {})
    return {"watched": [], "outbox": []} | data


def save_bili(rt: Any, data: dict[str, Any]) -> None:
    data["watched"] = data.get("watched", [])[-KEEP:]
    data["outbox"] = data.get("outbox", [])[-KEEP * 2 :]
    rt.repo.set_setting(BILI_KEY, json.dumps(data, ensure_ascii=False))


def switch(rt: Any, key: str, default: bool) -> bool:
    raw = (rt.repo.setting(key) or "").strip()
    if not raw:
        return default
    return raw == "on"


def set_switch(rt: Any, key: str, on: bool) -> None:
    rt.repo.set_setting(key, "on" if on else "off")


def clear(rt: Any) -> None:
    rt.repo.set_setting(CHECKIN_KEY, "")
    rt.repo.set_setting(BILI_KEY, "")
