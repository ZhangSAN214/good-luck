"""危机应对（docs/BRAIN.md「危机应对」；CLAUDE.md §5.1）。

用户在生活群或私聊里说出想不开、撑不下去这类话时，这个会话进入一段「关心时段」
（``chat.engine.care.hours``）：成员说话前拿到的不再是平时的意图卡，而是一张关心卡——
先认真关心、陪着说话、劝用户联系身边信任的人和专业帮助（求助电话按主时区挑），
不撒娇、不挽留、不开玩笑、不提自己的事。压过人设、挽留、恋爱和人的特质；大脑分区关着也照样生效。

- 只按词识别（程序、不调模型），**识别从宽**：宁可多关心一次，也不漏掉；「想死你了」「笑死」这类
  口头说法先去掉再判断。
- 关心时段按会话记（``member_care`` 键，只存本机）；同一会话里同事之间接话也照关心卡来。
- 只用于生活模式（工作群不挂载）。8 位同一套规则。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.storage.repository import iso

KEY = "member_care"
LEVELS = ("high", "medium")


class Care:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.care

    def enabled(self) -> bool:
        return bool(self.rt.config.roundtable.chat.engine.enabled and self.rules.enabled)

    def level(self, text: str) -> str:
        """这句话有多让人担心：high（有伤害自己的念头）/ medium（很难受、很绝望）/ 空串。"""
        if not text or not self.enabled():
            return ""
        low = text.lower()
        for phrase in self.rules.exclude:
            low = low.replace(phrase.lower(), " ")
        for name in LEVELS:
            if any(w.lower() in low for w in getattr(self.rules, name)):
                return name
        return ""

    def _windows(self) -> dict[str, dict[str, str]]:
        raw = self.rt.repo.setting(KEY)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def note(self, room: str, text: str, at: datetime) -> str:
        """用户说了这句话：够得上就给这个会话开（或延长）关心时段。返回级别。"""
        level = self.level(text)
        if not level:
            return ""
        windows = self._windows()
        until = at + timedelta(hours=self.rules.hours.get(level, 6.0))
        old = windows.get(room) or {}
        if old.get("until") and datetime.fromisoformat(old["until"]) > until:
            until = datetime.fromisoformat(old["until"])
        if old.get("level") == "high":
            level = "high"  # 同一时段里不降级
        windows[room] = {"level": level, "until": iso(until), "at": iso(at)}
        self.rt.repo.set_setting(KEY, json.dumps(windows, ensure_ascii=False))
        return level

    def active(self, room: str, at: datetime) -> str:
        """这个会话此刻在不在关心时段里：返回级别或空串。"""
        if not room or not self.enabled():
            return ""
        w = self._windows().get(room)
        if not w or datetime.fromisoformat(w["until"]) <= at:
            return ""
        return str(w.get("level") or "medium")

    def hotline(self, at: datetime) -> str:
        """按主时区挑求助电话：先找同名的时区，再找前缀（如 ``America/``），都没有就用默认的。"""
        zone = home_zone(self.rt)
        names = [zone.name(), str(zone.view(at).get("resolved") or "")]
        lines = self.rules.hotlines
        for name in names:
            if name in lines:
                return lines[name]
        for name in names:
            for prefix, line in lines.items():
                if prefix.endswith("/") and name.startswith(prefix):
                    return line
        return self.rules.default_hotline

    def card(self, level: str, at: datetime) -> str:
        text = self.rules.cards.get(level) or self.rules.cards.get("medium", "")
        return text.replace("{hotline}", self.hotline(at))
