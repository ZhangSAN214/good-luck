"""圈外熟人名册（2026-10-31 拟人插件调研第 9 项；规则在 ``chat.engine.circle``）。

成员嘴里会提到圈外的人：妈妈、室友、老同学、楼下奶茶店老板。模型每次随口编，就会前后对不上
（上周说室友叫小林，这周变成小王）。这里给每位记一本名册：

- **从人物卡来**：档案里「人际关系」「身世」两章提到的「我室友小林」「我妈」这类说法。
- **从她自己的话来**：她在生活群 / 私聊里说「我室友小林昨天……」时记下（带她的原话），不调模型。
  同一种关系最多记 ``per_relation`` 个名字，第一次说的为准；同事（另外几位成员）和你不记。
- **用的时候**：对方这条话提到她名册里的关系或名字（「你室友呢」）时，意图卡里带一句
  「你提过的：室友小林（你说过「……」）」，免得说反。只是事实，不让她主动去提。

只在圆桌世界里，不冒充现实中的人。8 位同一套规则；只存本机。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from roundtable.core.member.bus import Event, EventBus
from roundtable.core.storage.repository import iso

CIRCLE_KEY = "member_circle"
USER = "user"
LIFE_ROOMS = ("main", "dm:")
_NAME = r"(?:小|阿|老)[一-鿿]"


class Circle:
    def __init__(self, rt: Any, bus: EventBus | None = None) -> None:
        self.rt = rt
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.circle

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled and self.rules.relations)

    def _pattern(self) -> re.Pattern[str]:
        rels = sorted(self.rules.relations, key=len, reverse=True)
        alt = "|".join(re.escape(r) for r in rels)
        return re.compile(rf"(?:我|俺)(?:的|家)?({alt})({_NAME})?")

    def found(self, text: str) -> list[tuple[str, str, str]]:
        """一段话里的「我室友小林」：[(关系, 名字或空, 原句)]。"""
        out = []
        for m in self._pattern().finditer(text or ""):
            start = max(0, text.rfind("。", 0, m.start()) + 1)
            end = len(text)
            for p in "。！？!?\n":
                i = text.find(p, m.end())
                if i != -1:
                    end = min(end, i + 1)
            out.append((m.group(1), m.group(2) or "", text[start:end].strip()[:60]))
        return out

    # --- 存取 ------------------------------------------------------------------------

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        raw = self.rt.repo.setting(CIRCLE_KEY)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _card(self, member: str) -> list[dict[str, Any]]:
        from roundtable.core.member.profile import ProfileStore  # 档案依赖较多，用到再导入

        identity = ProfileStore(self.rt).current(member).data.get("identity") or {}
        text = "\n".join(str(identity.get(k) or "") for k in ("人际关系", "身世"))
        out: list[dict[str, Any]] = []
        for rel, name, quote in self.found(text):
            if not any(e["rel"] == rel and e["name"] == name for e in out):
                out.append({"rel": rel, "name": name, "quote": quote, "source": "card"})
        return out

    def entries(self, member: str) -> list[dict[str, Any]]:
        """她的名册：人物卡里的在前，再加她自己说过的。"""
        if not self.enabled():
            return []
        out = self._card(member)
        for e in self._load().get(member, []):
            if not any(x["rel"] == e["rel"] and x["name"] == e["name"] for x in out):
                out.append(e)
        return out

    def _others(self) -> set[str]:
        """同事的名字（另外几位成员）：不算圈外人。"""
        nick = self.rt.config.personas.nicknames
        return set(nick.values()) | set(nick.keys())

    def learn(self, member: str, text: str, at: datetime) -> int:
        """从她说的话里记下新提到的圈外人。返回新记了几个。"""
        if not self.enabled():
            return 0
        got = self.found(text)
        if not got:
            return 0
        known = self.entries(member)
        data = self._load()
        mine = data.get(member, [])
        others, added = self._others(), 0
        for rel, name, quote in got:
            if name and name in others:
                continue
            same = [e for e in known if e["rel"] == rel]
            if any(e["name"] == name for e in same):
                continue
            if not name and same:
                continue  # 「我妈」已经有了
            if name and len([e for e in same if e["name"]]) >= self.rules.per_relation:
                continue  # 第一次说的为准，别越编越多
            row = {"rel": rel, "name": name, "quote": quote, "at": iso(at), "source": "chat"}
            mine.append(row)
            known.append(row)
            added += 1
        if added:
            data[member] = mine[-self.rules.max_keep :]
            self.rt.repo.set_setting(CIRCLE_KEY, json.dumps(data, ensure_ascii=False))
        return added

    def on_event(self, event: Event) -> None:
        if event.kind != "chat.message" or not self.enabled():
            return
        room = str(event.data.get("room", ""))
        if event.data.get("from_user") or not room.startswith(LIFE_ROOMS):
            return
        row = self.rt.repo.chat_message(int(event.data.get("id", 0) or 0))
        if row is None or row["author"] in (USER, "system"):
            return
        self.learn(row["author"], row["text"] or "", event.at)

    # --- 用 --------------------------------------------------------------------------

    def line(self, member: str, last: str) -> str:
        """对方提到她名册里的关系或名字时，意图卡里的一句（没提到为空）。"""
        if not self.enabled() or not last:
            return ""
        hits = [
            e for e in self.entries(member) if e["rel"] in last or (e["name"] and e["name"] in last)
        ][: self.rules.max_line]
        if not hits:
            return ""
        items = "；".join(
            f"{e['rel']}{e['name']}（你说过「{e['quote']}」）"
            if e.get("quote")
            else e["rel"] + e["name"]
            for e in hits
        )
        return self.rules.line.format(items=items)

    def clear(self, member: str | None = None) -> None:
        data = self._load()
        if member is None:
            data = {}
        else:
            data.pop(member, None)
        self.rt.repo.set_setting(CIRCLE_KEY, json.dumps(data, ensure_ascii=False))
