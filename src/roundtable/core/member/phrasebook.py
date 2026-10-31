"""情境说法本（2026-10-31 拟人插件调研第 4 项；规则在 ``chat.engine.phrasebook``）。

真人会的不只是哪个词，而是「什么场合 → 怎么说」：被夸了推回去、被问会不会就绕一下再答、
朋友难受时嘴上损心里疼、看到好笑的事演一下……
思路借麦麦的「表达学习」，Python 自己写，全程序、不调模型。

- **招式**（``entries``）：每条写清什么场合（对方这条的语境 ``gists``、对方原话里的词 ``cues``、
  她这会儿的意图 ``intents``）和怎么说（``how``，只写说话逻辑，不给固定句子——10-31 用户：
  「不是照抄特定模板而是学会这套语言逻辑」）；谁爱用按性格分算（``base`` +
  Σ 系数 ×（性格分 - 3）），算出来不到 0 的不用。2026-10-31 用户转来的别人智能体发言拆成了
  8 条（开口亮态度、演起来、口是心非、大词说小事、先立后破、顺着句式多走一步、自问自答把球踢出去、
  碎句快节奏），见 ``docs/BRAIN.md``。
- **学来的**（``learn``）：你在生活群 / 私聊里接成员某种话时（她在吐槽、显摆、报喜……）回的短句，
  同一种场合说过 ``times`` 次以上就记下「这种时候有人爱说 X」，在乎你的成员会顺手学；
  ``forget_days`` 天你没再这么说就忘掉。
- 写进意图卡的一句：一张卡最多一条；同一条她 ``reuse_days`` 天内不重复用；已经有 ``max_users`` 位
  在 ``taken_days`` 天里用过的，别人不再挑（免得 8 位一个腔调）。
  对方难受（情绪危机）、她正委屈时不用。

只用于生活模式（意图卡），工作模式不挂载。8 位同一套规则，差别只来自性格分和她在不在乎你。
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.member.bus import Event, EventBus
from roundtable.core.storage.repository import iso

USER = "user"
LIFE_KINDS = frozenset({"group", "dm", "pair"})
LEARNED_KEY = "phrasebook:learned"
USED_KEY = "phrasebook:used"
TAG = "phrase:"


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 3.0


def _load(rt: Any, key: str) -> dict[str, Any]:
    raw = rt.repo.setting(key)
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class Phrasebook:
    def __init__(self, rt: Any, bus: EventBus | None = None) -> None:
        self.rt = rt
        if bus is not None:
            bus.subscribe("chat.message", self.on_event)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.phrasebook

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    # --- 挑一条 ----------------------------------------------------------------------

    @staticmethod
    def fit(entry: Any, traits: dict[str, Any]) -> float:
        return entry.base + sum(c * (_num(traits.get(t, 3)) - 3) for t, c in entry.traits.items())

    @staticmethod
    def matches(entry: Any, *, last: str, gist: str, chosen: str) -> bool:
        if entry.intents and chosen not in entry.intents:
            return False
        if not entry.gists and not entry.cues:
            return True
        if gist and gist in entry.gists:
            return True
        return any(re.search(p, last or "") for p in entry.cues)

    def _taken(self, name: str, member: str, at: datetime) -> tuple[bool, bool]:
        """(她自己 reuse_days 天内用过, 别人已经有 max_users 位在 taken_days 天里用过，
        或者别人 round_minutes 分钟内刚用过——同一轮里前面的人用过的形式，后面的人避开)。"""
        r = self.rules
        used = _load(self.rt, USED_KEY).get(name) or {}
        mine = used.get(member)
        recent_mine = bool(mine) and at - datetime.fromisoformat(mine) < timedelta(
            days=r.reuse_days
        )
        since = at - timedelta(days=r.taken_days)
        others = [datetime.fromisoformat(t) for m, t in used.items() if m != member]
        just = any(at - t < timedelta(minutes=r.round_minutes) for t in others)  # 前面的人刚用过
        return recent_mine, just or sum(1 for t in others if t >= since) >= r.max_users

    def line(
        self,
        member: str,
        *,
        last: str,
        gist: str,
        chosen: str,
        traits: dict[str, Any],
        care: float,
        hurt: bool,
        at: datetime,
        rng: random.Random,
    ) -> tuple[str, str] | None:
        """返回 (标签, 写进意图卡的一句)；这会儿没有合适的就 None。"""
        r = self.rules
        if not self.enabled() or hurt or gist in r.skip_gists:
            return None
        pool: list[tuple[float, str, str]] = []  # (权重, 名字, 那一句)
        for entry in r.entries:
            if not self.matches(entry, last=last, gist=gist, chosen=chosen):
                continue
            weight = self.fit(entry, traits)
            if weight <= 0:
                continue
            mine, full = self._taken(entry.name, member, at)
            if mine or full:
                continue
            pool.append((weight, entry.name, r.line.format(how=entry.how)))
        lr = r.learn
        if lr.enabled and care >= lr.care and gist and chosen in lr.intents:
            for said in self.learned(gist, at):
                name = f"学:{gist}:{said}"
                mine, full = self._taken(name, member, at)
                if not (mine or full):
                    pool.append((lr.weight, name, lr.line.format(said=said)))
        if not pool or rng.random() >= r.chance:
            return None
        total = sum(w for w, _, _ in pool)
        roll, acc = rng.random() * total, 0.0
        for weight, name, text in pool:
            acc += weight
            if roll <= acc:
                return f"{TAG}{name}", text
        return f"{TAG}{pool[-1][1]}", pool[-1][2]

    def after(self, member: str, tags: tuple[str, ...], at: datetime) -> None:
        names = [t[len(TAG) :] for t in tags if t.startswith(TAG)]
        if not names:
            return
        used = _load(self.rt, USED_KEY)
        since = iso(at - timedelta(days=max(self.rules.taken_days, self.rules.reuse_days)))
        for name in names:
            used.setdefault(name, {})[member] = iso(at)
        used = {  # 过期的清掉
            n: {m: t for m, t in who.items() if t >= since} for n, who in used.items()
        }
        used = {n: who for n, who in used.items() if who}
        self.rt.repo.set_setting(USED_KEY, json.dumps(used, ensure_ascii=False))

    # --- 跟你学 ----------------------------------------------------------------------

    def learned(self, gist: str, at: datetime) -> list[str]:
        lr = self.rules.learn
        since = iso(at - timedelta(days=lr.forget_days))
        book = _load(self.rt, LEARNED_KEY).get(gist) or {}
        return [
            s for s, v in book.items() if v.get("n", 0) >= lr.times and v.get("last", "") >= since
        ]

    def on_event(self, event: Event) -> None:
        """你在生活群 / 私聊里接了成员一句话：记「她那种话 → 你这么回」。"""
        lr = self.rules.learn
        if not (self.enabled() and lr.enabled and event.data.get("from_user")):
            return
        room = str(event.data.get("room", ""))
        info = self.rt.repo.chat_room(room) or {}
        if info.get("kind", "") not in LIFE_KINDS:
            return
        msg_id = int(event.data.get("id", 0) or 0)
        msg = self.rt.repo.chat_message(msg_id)
        if not msg:
            return
        from roundtable.core.chat_actions import STICKER_RE
        from roundtable.core.member.lingo import Lingo  # lingo 不依赖本模块的类

        said = STICKER_RE.sub("", str(msg["text"] or "")).strip()
        if not (2 <= len(said) <= lr.max_chars) or "\n" in said or said.startswith(("http", "/")):
            return
        rows = self.rt.repo.chat_messages(room, limit=6)
        before = [m for m in rows if m["id"] < msg_id and m["author"] != "system"]
        if not before or before[-1]["author"] == USER:
            return
        gist = Lingo(self.rt).gist(before[-1]["text"] or "", before[-1].get("meta"))
        if gist is None or gist.kind not in lr.kinds:
            return
        data = _load(self.rt, LEARNED_KEY)
        book = data.setdefault(gist.kind, {})
        seen = book.setdefault(said, {"n": 0, "first": iso(event.at)})
        seen["n"] = int(seen.get("n", 0)) + 1
        seen["last"] = iso(event.at)
        since = iso(event.at - timedelta(days=lr.forget_days))
        book = {s: v for s, v in book.items() if v.get("last", "") >= since}
        data[gist.kind] = dict(
            sorted(book.items(), key=lambda kv: (kv[1]["n"], kv[1]["last"]))[-lr.keep :]
        )
        self.rt.repo.set_setting(LEARNED_KEY, json.dumps(data, ensure_ascii=False))
