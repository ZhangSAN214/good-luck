"""主时区：整个圆桌按哪个时区过日子（公寓作息、群聊时间、日记、每晚整理……）。

用户在终端时钟里点星星选定，存在 ``app_settings``；没选过或选了「跟随本机」时用电脑的时区。
存的是 IANA 名字（如 ``Europe/London``），夏令时由 zoneinfo 处理。只存本机。
"""

from __future__ import annotations

import contextlib
from datetime import datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

KEY = "home_tz"
LOCAL = "local"


def parse(name: str | None) -> tzinfo | None:
    """IANA 名字 → 时区；``local``、空或无效 → None（本机时区）。"""
    if not isinstance(name, str) or not name or name == LOCAL:
        return None
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def valid(name: str) -> bool:
    return name == LOCAL or parse(name) is not None


class HomeZone:
    """读写主时区；读到的结果缓存，改了立即生效。"""

    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self._name: str | None = None

    def name(self) -> str:
        if self._name is None:
            saved = self.repo.setting(KEY) or LOCAL
            self._name = saved if valid(saved) else LOCAL
        return self._name

    def tz(self) -> tzinfo | None:
        """None 表示本机时区（``astimezone(None)`` 正好是本机）。"""
        return parse(self.name())

    def set(self, name: str) -> str:
        name = (name or "").strip() or LOCAL
        if not valid(name):
            raise ValueError(f"不认识的时区：{name}")
        self.repo.set_setting(KEY, name)
        self._name = name
        return name

    def local(self, moment: datetime) -> datetime:
        """换算成主时区的时间（带时区）。"""
        return moment.astimezone(self.tz())

    def view(self, now: datetime) -> dict[str, Any]:
        here = self.local(now)
        off = here.utcoffset()
        minutes = int(off.total_seconds() // 60) if off is not None else 0
        return {
            "zone": self.name(),
            # 用户选过没有（没选过时网页把时钟的主时区报上来）
            "chosen": self.repo.setting(KEY) is not None,
            "resolved": getattr(here.tzinfo, "key", None) or here.tzname(),
            "offset_minutes": minutes,
            "now": here.isoformat(timespec="seconds"),
        }


def home_zone(rt: Any) -> HomeZone:
    """运行时上共用一个 HomeZone（没有就建一个），各模块从这里取主时区。"""
    found = getattr(rt, "home_zone", None)
    if isinstance(found, HomeZone):
        return found
    zone = HomeZone(rt.repo)
    with contextlib.suppress(AttributeError):  # 测试里的简易替身不让加属性：不缓存也能用
        rt.home_zone = zone
    return zone
