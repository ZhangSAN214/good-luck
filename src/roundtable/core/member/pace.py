"""回话节拍（2026-10-31 拟人插件调研第 2 项；规则在 ``chat.engine.pace``）。

真人回消息有节拍：你连着发几条，她会等你说完再回；在健身、吃饭时晚一会儿才看到；
打字有快慢；回的是前面那句时会引用一下；互道晚安以后你再回个「嗯嗯」，她就不接了。

- ``debounce``：私聊里你连着发时等多久再一起想（按你这一串的间隔自适应）。
- ``busy_delay``：她在忙时私聊晚多久回——从你这一串（她上次回你之后）的第一条算起，
  所以你接着发不会让她越拖越久；刚来回聊过、说出让人担心的话时不晚回。
- ``gap``：两条气泡之间停多久（这条的字数 ÷ 她的打字速度，带一点随机）。
- ``quote``：她想好时后面已经又有几条别人的话，第一条该不该引用她回的那条。
- ``wrapup``：收尾不回（私聊里她上一句已经道别、你又只回了句应声）。

全程序、不调模型；只用于生活群和私聊。8 位同一套公式，差别只来自作息和性格分。
"""

from __future__ import annotations

import random
import re
import statistics
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.hometz import home_zone
from roundtable.core.office.apartment import schedule

USER = "user"
_STRIP = re.compile(r"[\s，,。.！!？?~～…、]+|\[表情:[^\]]*\]")


def _num(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 3.0


class Pace:
    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.pace

    def enabled(self) -> bool:
        engine = self.rt.config.roundtable.chat.engine
        return bool(engine.enabled and self.rules.enabled)

    # --- 等你说完 --------------------------------------------------------------------

    def _user_burst(self, room: str, at: datetime) -> list[datetime]:
        """你最近连着发的这一串（相邻两条不超过 burst_seconds）的时间，从早到晚。"""
        rows = self.rt.repo.chat_messages(room, limit=20)
        times: list[datetime] = []
        for r in reversed(rows):
            if r["author"] != USER:
                break
            t = datetime.fromisoformat(r["created_at"])
            if times and (times[-1] - t).total_seconds() > self.rules.burst_seconds:
                break
            times.append(t)
        return list(reversed(times))

    def debounce(self, room: str, at: datetime) -> float:
        r = self.rules
        if not self.enabled() or r.debounce_max <= 0:
            return 0.0
        times = self._user_burst(room, at)
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)]
        if not gaps:
            return r.debounce_min
        return max(r.debounce_min, min(r.debounce_max, statistics.median(gaps) * 1.3))

    # --- 忙时晚回 --------------------------------------------------------------------

    def activity(self, member: str, at: datetime) -> str:
        local = home_zone(self.rt).local(at)
        return schedule(member, local.hour, local.weekday())[1]

    def _since_her_last(self, member: str, room: str) -> tuple[datetime | None, datetime | None]:
        """(她在这里最后一次说话的时间, 那之后你的第一条的时间)。"""
        rows = self.rt.repo.chat_messages(room, limit=50)
        mine, first = None, None
        for r in reversed(rows):
            if r["author"] == member:
                mine = datetime.fromisoformat(r["created_at"])
                break
            if r["author"] == USER:
                first = datetime.fromisoformat(r["created_at"])
        return mine, first

    def busy_delay(self, member: str, room: str, text: str, at: datetime) -> float:
        """私聊：她在忙时还要等几秒才回（0 = 现在就回）。"""
        r = self.rules
        if not self.enabled() or not r.busy:
            return 0.0
        span = r.busy.get(self.activity(member, at))
        if not span or len(span) != 2:
            return 0.0
        from roundtable.core.member.lingo import Lingo  # lingo 不依赖本模块

        gist = Lingo(self.rt).gist(text, None)
        if gist is not None and gist.kind == "crisis":
            return 0.0
        last, first = self._since_her_last(member, room)
        if last is not None and at - last <= timedelta(minutes=r.hot_minutes):
            return 0.0  # 刚来回聊过：秒回
        start = first or at
        rng = random.Random(f"pace:{member}:{room}:{start.isoformat()}")
        from roundtable.core.member.phase import Phase  # 生活阶段：忙季、赶东西时回得更慢

        stretch = 1.0 + Phase(self.rt).busy(member, at)
        wait = min(rng.uniform(min(span), max(span)) * stretch, r.busy_cap)
        return max(0.0, wait - (at - start).total_seconds())

    # --- 打字 ------------------------------------------------------------------------

    def _traits(self, member: str) -> dict[str, Any]:
        from roundtable.core.member.profile import ProfileStore

        try:
            data = ProfileStore(self.rt).current(member).data
        except Exception:  # noqa: BLE001 — 没有档案：按中间分
            return {}
        return dict((data.get("personality") or {}).get("五项") or {})

    def speed(self, member: str) -> float:
        r = self.rules
        traits = self._traits(member)
        scale = 1 + sum(c * (_num(traits.get(t, 3)) - 3) for t, c in r.type_traits.items())
        return r.type_cps * max(0.3, scale)

    def gap(self, member: str, text: str, base: float) -> float:
        """两条气泡之间停多久；没开时用 base（原来的固定间隔）。"""
        if not self.enabled():
            return base
        r = self.rules
        chars = len(_STRIP.sub("", text or "")) or 1
        jitter = random.Random(f"gap:{member}:{text}").uniform(0.8, 1.25)
        return max(r.gap_min, min(r.gap_max, chars / self.speed(member) * jitter))

    # --- 引用与收尾 ------------------------------------------------------------------

    def quote(self, member: str, room: str, reply_to: int) -> bool:
        """她回的那条后面已经又有 quote_after 条别人的话：该引用一下。"""
        n = self.rules.quote_after
        if not self.enabled() or not reply_to or n <= 0:
            return False
        rows = self.rt.repo.chat_messages(room, after=reply_to, limit=n + 5)
        return sum(1 for m in rows if m["author"] not in (member, "system")) >= n

    def wrapup(self, member: str, room: str, reply_to: int) -> bool:
        """私聊收尾：她上一句已经道别，你这条只是应声——不回了。"""
        r = self.rules
        if not self.enabled() or not r.acks or not reply_to:
            return False
        row = self.rt.repo.chat_message(reply_to)
        if row is None or row["author"] != USER:
            return False
        said = _STRIP.sub("", row["text"] or "").lower()
        acks = {_STRIP.sub("", a).lower() for a in r.acks}
        if not said or (said not in acks and not re.fullmatch(r"(哈|嘿|嗯|好)+", said)):
            return False
        rows = self.rt.repo.chat_messages(room, limit=20)
        before = [m for m in rows if m["id"] < reply_to and m["author"] != "system"]
        if not before or before[-1]["author"] != member:
            return False
        last = before[-1]["text"] or ""
        return any(b in last for b in r.byes)
