"""以后别再提（2026-10-31，借 AI-Buddy 的思路；规则在 ``chat.engine.hush``）。

你说「别提了」「以后别再说这个」「别再提考试了」时，那件事不删，但她们不再主动提，你自己问起才说。
真人朋友就是这样：知道你不想聊，就绕开。全程序、不调模型。

- **记下**：只认你在生活群 / 私聊里一句短话（``max_chars`` 字内）
  开头就说「别提 / 别再说 / 不要再聊」的。
  说了具体的事（「别再提考试了」）就记那件事；只说「别提了」就记上一位成员刚说的那句。
- **谁守**：私聊里说的只有那位守；群里说的，群里的人都守。
- **怎么守**：聊天时「关于你」后面带一句「他说过别再提：……（别主动提，他自己问起再说）」；
  她念头池里提到那件事的新鲜事也不再冒出来。
- **放开**：你说「可以提了」「没事了可以说」时，放开最近一件。

只存本机；8 位同一套规则。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from roundtable.core.member.bus import Event, EventBus
from roundtable.core.storage.repository import iso

HUSH_KEY = "user_hush"
WORK = "work"
USER = "user"
_VAGUE = re.compile(r"^(这个|这事|这件事|那个|那件事|那事|它|了|啦|吧|这些|那些)*$")


class Hush:
    def __init__(self, rt: Any, bus: EventBus | None = None) -> None:
        self.rt = rt
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.hush

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    def _load(self) -> list[dict[str, Any]]:
        raw = self.rt.repo.setting(HUSH_KEY)
        try:
            data = json.loads(raw) if raw else []
        except ValueError:
            return []
        return data if isinstance(data, list) else []

    def _save(self, data: list[dict[str, Any]]) -> None:
        self.rt.repo.set_setting(HUSH_KEY, json.dumps(data, ensure_ascii=False))

    # --- 记下 / 放开 -----------------------------------------------------------------

    def heard(self, room: str, text: str, msg_id: int, at: datetime) -> str:
        """你这条话是不是「别再提」或「可以提了」：返回 hush / lift / 空串。"""
        if not self.enabled():
            return ""
        text = (text or "").strip()
        if not text or len(text) > self.rules.max_chars:
            return ""
        data = self._load()
        if re.search(self.rules.lift, text):
            if not data:
                return ""
            self._save(data[:-1])
            return "lift"
        m = re.match(self.rules.ask, text)
        if m is None:
            return ""
        topic = re.sub(r"[了啦吧呀啊哈。！!~～，,\s]+$", "", m.group(1) or "").strip()
        topic = re.sub(r"^(这个|那个|关于)", "", topic)
        quote = ""
        if not topic or _VAGUE.match(topic):
            topic = ""
            prev = [
                r
                for r in self.rt.repo.chat_messages(room, limit=10)
                if r["id"] < msg_id and r["author"] not in (USER, "system")
            ]
            if not prev:
                return ""
            quote = " ".join((prev[-1]["text"] or "").split())[:30]
        item = {"topic": topic, "quote": quote, "room": room, "at": iso(at)}
        data = [d for d in data if (d["topic"], d["quote"]) != (topic, quote)] + [item]
        self._save(data[-self.rules.max_keep :])
        return "hush"

    def on_event(self, event: Event) -> None:
        if event.kind != "chat.message" or not event.data.get("from_user"):
            return
        room = str(event.data.get("room", ""))
        if not room or room == WORK:  # 工作群不管
            return
        row = self.rt.repo.chat_message(int(event.data.get("id", 0) or 0))
        if row is None or row["author"] != USER:
            return
        self.heard(room, row["text"] or "", row["id"], event.at)

    # --- 守 --------------------------------------------------------------------------

    def items(self, member: str) -> list[dict[str, Any]]:
        """她要守的：私聊里跟她说的，加上群里说的。"""
        if not self.enabled():
            return []
        mine = f"dm:{member}"
        return [d for d in self._load() if not d["room"].startswith("dm:") or d["room"] == mine]

    def line(self, member: str) -> str:
        items = self.items(member)
        if not items:
            return ""
        what = "；".join(d["topic"] or f"你说「{d['quote']}」那件事" for d in items)
        return self.rules.line.format(items=what)

    def blocks(self, member: str, text: str) -> bool:
        """这段话碰到她要守的、说得明白的事（念头池用）。"""
        return any(d["topic"] and d["topic"] in text for d in self.items(member))

    def clear(self) -> None:
        self._save([])
