"""「第一次」纪念（2026-10-31 拟人插件调研第 10 项；规则在 ``chat.engine.firsts``）。

你在私聊里亲口说的「第一次」（「第一次跟人说这些」「我第一次自己做饭」）记下来，带上原话；
过了整月、整年的那天，她可能自然地轻轻提一句。思路借「我会永远陪着你」的互动纪念日，
Python 自己写，全程序、不调模型。

- 只认你自己的原话里写了「第一次」的，不猜、不编；同一件事只记一次。
- 到了 ``milestones``（几个月后）的同一天，意图卡里有 ``chance`` 的机会带一句，**只提这一次**
  （真说了才算提过），不要你回应。没有打卡、没有连续天数，不会因为你没回就失落（§5.1 第 2 条）。
- 关心时段不提（那时意图卡只给关心卡）。只存本机。8 位同一套规则。
"""

from __future__ import annotations

import calendar
import json
import random
import re
from datetime import date, datetime
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.storage.repository import iso

FIRSTS_KEY = "member_firsts"
TAG = "first:"


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, m + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


class Firsts:
    def __init__(self, rt: Any, bus: EventBus | None = None) -> None:
        self.rt = rt
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.firsts

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled and self.rules.milestones)

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        raw = self.rt.repo.setting(FIRSTS_KEY)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
        self.rt.repo.set_setting(FIRSTS_KEY, json.dumps(data, ensure_ascii=False))

    def entries(self, member: str) -> list[dict[str, Any]]:
        return list(self._load().get(member, []))

    # --- 记 --------------------------------------------------------------------------

    def found(self, text: str) -> tuple[str, str] | None:
        """你这句话里的「第一次……」：(那件事, 原句)。"""
        m = re.search(self.rules.pattern, text or "")
        if m is None:
            return None
        what = m.group(1).strip(" ，,。.!！?？~～")
        if len(what) < 2:
            return None
        return what, (text or "").strip()[:60]

    def record(self, member: str, text: str, at: datetime) -> bool:
        if not self.enabled():
            return False
        got = self.found(text)
        if got is None:
            return False
        what, quote = got
        data = self._load()
        mine = data.get(member, [])
        if any(e["what"] == what for e in mine):
            return False
        n = max((int(e.get("id", 0)) for e in mine), default=0) + 1
        mine.append({"id": n, "what": what, "quote": quote, "at": iso(at), "said": []})
        data[member] = mine[-self.rules.max_keep :]
        self._save(data)
        return True

    def on_event(self, event: Event) -> None:
        if event.kind != "chat.message" or not event.data.get("from_user") or not self.enabled():
            return
        room = str(event.data.get("room", ""))
        if not room.startswith("dm:"):
            return  # 只记私聊里对她一个人说的
        row = self.rt.repo.chat_message(int(event.data.get("id", 0) or 0))
        if row is None or row["author"] != "user":
            return
        self.record(room.removeprefix("dm:"), row["text"] or "", event.at)

    # --- 提 --------------------------------------------------------------------------

    def _when(self, months: int) -> str:
        if months % 12 == 0:
            return "一年前" if months == 12 else f"{months // 12} 年前"
        return "一个月前" if months == 1 else f"{months} 个月前"

    def line(self, member: str, at: datetime, rng: random.Random) -> tuple[str, str] | None:
        """今天是哪件「第一次」的整月 / 整年：(标记, 意图卡里的一句)；没有或没掷中为 None。"""
        if not self.enabled():
            return None
        zone = home_zone(self.rt)
        today = zone.local(at).date()
        for e in self.entries(member):
            try:
                then = zone.local(datetime.fromisoformat(e["at"])).date()
            except (KeyError, ValueError):
                continue
            for months in self.rules.milestones:
                if months in e.get("said", []) or add_months(then, months) != today:
                    continue
                if rng.random() >= self.rules.chance:
                    return None
                text = self.rules.line.format(
                    when=self._when(months), what=e["what"], quote=e["quote"]
                )
                return f"{TAG}{e['id']}:{months}", text
        return None

    def after(self, member: str, tags: tuple[str, ...] | list[str]) -> None:
        """真说了才算提过：同一件事的同一个纪念日只提一次。"""
        marks = [t[len(TAG) :].split(":") for t in tags if t.startswith(TAG)]
        if not marks:
            return
        data = self._load()
        for e in data.get(member, []):
            for n, months in marks:
                if str(e.get("id")) == n and int(months) not in e.get("said", []):
                    e.setdefault("said", []).append(int(months))
        self._save(data)

    def clear(self, member: str | None = None) -> None:
        data = {} if member is None else {k: v for k, v in self._load().items() if k != member}
        self._save(data)
