"""你的状态（成员系统 P8 第二块）：正在放的歌、电脑空闲、在玩游戏、课表和 DDL、邮件栏。

- **每项默认关**，开关只由「关于你」（``about.py``）写；这里只读开关、问来源、拼一句话。
- **只在聊天时现问**：私聊和生活群里成员要回话时问一次，各项拼成一段（≤ ``status_chars`` 字，
  8 位相同），接在「关于用户」后面。读到的歌名、程序、邮件数都不写数据库。
- 来源由服务层装上（``providers``：种类 → 函数(本地时间) → 一句话或空），测试里换成假的；
  某项来源不可用时这一项不说（画像页显示原因）。
- 工作模式不挂载（``line(..., mode="work")`` 报错，有测试）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member.about import STATUS_KINDS, AboutYou
from roundtable.core.member.mode import Mode, require_life

Provider = Callable[[datetime], str | None]
Reason = Callable[[], str | None]


class UserStatus:
    def __init__(
        self,
        rt: Any,
        providers: dict[str, Provider] | None = None,
        reasons: dict[str, Reason] | None = None,
    ) -> None:
        self.rt = rt
        self.about = AboutYou(rt)
        self.providers = dict(providers or {})
        self.providers.setdefault("timetable", self.about.timetable_line)
        self.reasons = dict(reasons or {})

    def now(self) -> datetime:
        return datetime.now(home_zone(self.rt).tz())

    def _say(self, kind: str, now: datetime) -> str:
        fn = self.providers.get(kind)
        if fn is None:
            return ""
        try:
            return " ".join((fn(now) or "").split())
        except Exception:  # noqa: BLE001 - 读不到就不说，不影响聊天
            return ""

    def line(self, mode: Mode | str = Mode.LIFE, now: datetime | None = None) -> str:
        """打开了的几项拼成一段；都没开或都没东西可说时为空串。"""
        require_life(mode)
        if not self.about.enabled():
            return ""
        r = self.about.rules
        now = now or self.now()
        on = self.about.switches()["status"]
        parts = [t for k in STATUS_KINDS if on.get(k) and (t := self._say(k, now))]
        text = "；".join(parts)
        if not text or r.status_chars == 0:
            return ""
        if len(text) > r.status_chars:
            text = text[: r.status_chars - 1].rstrip("；") + "…"
        return f"{r.status_header}\n{text}"

    def view(self, now: datetime | None = None) -> dict[str, Any]:
        """画像页用：每项开没开、能不能读、现在成员会看到什么。"""
        now = now or self.now()
        sw = self.about.switches()
        items = []
        for kind, label in STATUS_KINDS.items():
            reason = self.reasons.get(kind, lambda: None)()
            if kind != "timetable" and kind not in self.providers:
                reason = reason or "这台电脑上用不了"
            items.append(
                {
                    "kind": kind,
                    "label": label,
                    "on": bool(sw["status"].get(kind)),
                    "reason": reason,
                    "preview": "" if reason else self._say(kind, now),
                }
            )
        return {
            "items": items,
            "line": self.line(now=now),
            "music_player": sw["music_player"],
            "games": sw["games"],
            "config_games": self.about.rules.games,
            "schedule": self.about.schedule(),
        }
