"""话头本（2026-11-01 你说：她们没有聊天欲望、不会自然找话题，问了也没人接着问）。

真人聊天有「钩子」：你说过要做的事，过两天会被问「后来咋样了」；她问了你一句你没回，
下次见面可能顺口再提。这里全程序记下这些话头，到时候在意图卡里给一句「可以接上」，
接上一次就划掉。不调模型，8 位同一套规则；只存本机，只在生活群和私聊里用。

- **你说过要做的事**：你在生活群 / 私聊里说「明天考试」「下周三组会」「周末去爬山」这类
  带日子的话，记下原话和大概哪天；那天过了之后几天里（``stale_days``），听到过的人
  （私聊里说的只有那位，群里说的大家都听到了）有 ``chance`` 的机会问一句后来怎样。
  有人真问了就划掉（群里说的只问一次，不会八个人轮着问）。
- **她问了你、你没回**：私聊里她最后一句是在问你，你隔了 ``gap_hours`` 以上才回来、说的又是
  别的事，她有 ``q_chance`` 的机会顺口再提一句，也可以算了。同一句只提一次。
- 主动找你（``reach.py``）把「你说过的事那天过了」也算成想找你的由头。
"""

from __future__ import annotations

import json
import random
import re
from datetime import date, datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.member.bus import Event, EventBus
from roundtable.core.storage.repository import iso

HOOKS_KEY = "member_hooks"
TAG = "hook:"
USER = "user"
WORK = "work"
WEEK = "一二三四五六日"
_DAY = r"([一二三四五六日天])"


def _weekday(ch: str) -> int:
    return 6 if ch in "日天" else WEEK.index(ch)


def due_of(text: str, today: date) -> date | None:
    """这句话说的是哪天的事（大概）；没说日子为 None。"""
    t = text or ""
    if "后天" in t:
        return today + timedelta(days=2)
    if re.search(r"明天|明早|明晚|明儿", t):
        return today + timedelta(days=1)
    if m := re.search(r"下(?:周|星期|礼拜)" + _DAY, t):
        monday = today + timedelta(days=7 - today.weekday())
        return monday + timedelta(days=_weekday(m.group(1)))
    if re.search(r"下(?:周|星期|礼拜)", t):
        return today + timedelta(days=7)
    if m := re.search(r"(?:这|本)?(?:周|星期|礼拜)" + _DAY, t):
        return today + timedelta(days=(_weekday(m.group(1)) - today.weekday()) % 7)
    if "周末" in t:
        return today + timedelta(days=max(0, 5 - today.weekday()))
    if re.search(r"今晚|今天晚上|今天下午|待会|等会", t):
        return today
    if "下个月" in t:
        return today + timedelta(days=30)
    return None


class Hooks:
    def __init__(self, rt: Any, bus: EventBus | None = None) -> None:
        self.rt = rt
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.hooks

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    def _load(self) -> dict[str, Any]:
        raw = self.rt.repo.setting(HOOKS_KEY)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("plans", [])
        data.setdefault("asked", [])  # 她问了你、你没回：已经再提过的那几句（消息 id）
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.rt.repo.set_setting(HOOKS_KEY, json.dumps(data, ensure_ascii=False))

    def _today(self, at: datetime) -> date:
        return home_zone(self.rt).local(at).date()

    # --- 记 --------------------------------------------------------------------------

    def heard(self, room: str, text: str, at: datetime) -> bool:
        """你说了一句带日子的打算：记下来。返回记没记。"""
        if not self.enabled():
            return False
        text = " ".join((text or "").split())
        if not (self.rules.min_chars <= len(text) <= self.rules.max_chars):
            return False
        if re.search(r"[？?]", text):  # 问别人「明天干嘛」不算你的打算
            return False
        due = due_of(text, self._today(at))
        if due is None:
            return False
        data = self._load()
        if any(p["quote"] == text for p in data["plans"]):
            return False
        n = max((int(p.get("id", 0)) for p in data["plans"]), default=0) + 1
        row = {"id": n, "quote": text, "due": due.isoformat(), "room": room, "at": iso(at)}
        data["plans"] = (data["plans"] + [row | {"done": False}])[-self.rules.max_keep :]
        self._save(data)
        return True

    def on_event(self, event: Event) -> None:
        if event.kind != "chat.message" or not event.data.get("from_user"):
            return
        room = str(event.data.get("room", ""))
        if not room or room == WORK or not (room == "main" or room.startswith("dm:")):
            return
        row = self.rt.repo.chat_message(int(event.data.get("id", 0) or 0))
        if row is None or row["author"] != USER:
            return
        self.heard(room, row["text"] or "", event.at)

    # --- 到时候 ----------------------------------------------------------------------

    def due(self, member: str, at: datetime) -> list[dict[str, Any]]:
        """她听到过、那天已经过了、还没人问过的打算。"""
        if not self.enabled():
            return []
        today = self._today(at)
        mine = f"dm:{member}"
        out = []
        for p in self._load()["plans"]:
            if p.get("done") or (p["room"].startswith("dm:") and p["room"] != mine):
                continue
            try:
                d = date.fromisoformat(p["due"])
            except ValueError:
                continue
            if d < today <= d + timedelta(days=self.rules.stale_days):
                out.append(p)
        return out

    def _dangling(self, member: str, room: str, at: datetime) -> dict[str, Any] | None:
        """私聊里她上次问你的话你没接：隔了好久你才回来、说的是别的。"""
        if room != f"dm:{member}":
            return None
        rows = self.rt.repo.chat_messages(room, limit=6)
        users = [i for i, r in enumerate(rows) if r["author"] == USER]
        if not users or users[-1] != len(rows) - 1:
            return None
        before = [r for r in rows[: users[-1]] if r["author"] != "system"]
        if not before or before[-1]["author"] != member:
            return None
        q, now_row = before[-1], rows[-1]
        if not re.search(r"[？?]", q["text"] or "") or q["id"] in self._load()["asked"]:
            return None
        gap = datetime.fromisoformat(now_row["created_at"]) - datetime.fromisoformat(
            q["created_at"]
        )
        if gap < timedelta(hours=self.rules.gap_hours):
            return None
        return q

    def line(
        self, member: str, at: datetime, rng: random.Random, room: str = ""
    ) -> tuple[str, str] | None:
        """意图卡里的一句话头：(标记, 那句)；没有或没掷中为 None。"""
        if not self.enabled():
            return None
        r = self.rules
        q = self._dangling(member, room, at) if room else None
        if q is not None and rng.random() < r.q_chance:
            quote = " ".join((q["text"] or "").split())[:40]
            return f"{TAG}q:{q['id']}", r.q_line.format(quote=quote)
        plans = self.due(member, at)
        if plans and rng.random() < r.chance:
            p = plans[0]
            return f"{TAG}p:{p['id']}", r.line.format(quote=p["quote"])
        return None

    def after(self, member: str, tags: tuple[str, ...] | list[str]) -> None:
        """真说了才划掉：你的打算有人问过就不再问；她再提过的那句不再提。"""
        marks = [t[len(TAG) :].split(":", 1) for t in tags if t.startswith(TAG)]
        if not marks:
            return
        data = self._load()
        for kind, ref in marks:
            if kind == "q":
                data["asked"] = (data["asked"] + [int(ref)])[-200:]
            for p in data["plans"]:
                if kind == "p" and str(p.get("id")) == ref:
                    p["done"] = True
        self._save(data)

    def clear(self) -> None:
        self._save({"plans": [], "asked": []})
