"""旅行（L5，docs/LIFE_STORY.md「旅行」）。不调用模型，只看她自己的生活和档案。

- **偶尔出门**：每天按 ``chance`` 想出门（档案提到 ``like_words`` 时 × ``boost``），冷却
  ``cooldown_days``；同一时间最多 ``max_away`` 位在外面。目的地按档案里的爱好（``likes``）和月份挑，
  短途为主（配置里大多是 0～2 晚）。
- **前后因果**：开始那天就把整趟排好：计划（今天）→ 前一天晚上收拾行李 → 出发 → 途中每天一件事
  （最多 3 件）→ 回来给常一起玩的朋友带东西 → 当晚有点累早睡。
  每一步写成 ``travel_<步>`` 的生活事件。
- **不在家**：出发到回来前的那几天，空闲时段和吃饭睡觉都在「外地」（公寓里显示不在家）；
  **办公室工作照常**（用户定，上班时段照作息表，不动办公室的任何东西）。
- 状态存在她自己的生活状态里（``travel`` 进行中的这趟、``trips`` 去过哪儿）。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from roundtable.core.member.story import store

PREFIX = "travel_"
AWAY = "away"  # 生活事件 / 去处里的「外地」
WEEKDAY = "一二三四五六日"
MAX_SIGHTS = 3
KEEP_TRIPS = 12
# 每一步的事件 id → (钟点, 地点, 交给 P3 的标签)
STEPS: dict[str, tuple[tuple[int, int], str, list[str]]] = {
    "travel_plan": ((19, 23), "dorm", []),
    "travel_pack": ((20, 23), "dorm", []),
    "travel_go": ((7, 10), AWAY, ["story.travel"]),
    "travel_trip": ((11, 18), AWAY, ["story.trip"]),
    "travel_back": ((16, 20), "dorm", ["story.back"]),
    "travel_rest": ((21, 24), "dorm", []),
}


def tags_of(story: str) -> list[str]:
    return list(STEPS.get(story, ((0, 0), "", []))[2])


@dataclass(frozen=True)
class Leg:
    """一趟旅行里的一步：开始那天后第几天、事件 id、话、同行 / 收礼物的人。"""

    day: int
    story: str
    text: str
    other: str = ""

    @property
    def hours(self) -> tuple[int, int]:
        return STEPS[self.story][0]

    @property
    def place(self) -> str:
        return STEPS[self.story][1]


class Travel:
    def __init__(self, rt: Any, text: Callable[[str], str]) -> None:
        self.rt = rt
        self.text = text  # 成员 → 档案全文
        self.states: dict[str, dict[str, Any]] = {}  # 这次推进里各人的生活状态（看谁在外面）

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.life.story.travel

    def enabled(self) -> bool:
        r = self.rules
        return self.rt.config.roundtable.chat.life.story.enabled and r.enabled and bool(r.dests)

    # --- 读 -------------------------------------------------------------------------

    @staticmethod
    def trip_on(st: dict[str, Any], day: date) -> dict[str, Any] | None:
        """这天她在外面的那一趟（出发当天到回来前一天）；不在外面为 None。"""
        trip = st.get("travel") or {}
        if not trip.get("go"):
            return None
        d = day.isoformat()
        return trip if trip["go"] <= d < trip["back"] or trip["go"] == trip["back"] == d else None

    @staticmethod
    def out_on(st: dict[str, Any], day: date) -> bool:
        """这天在路上（出发那天到回来那天，含回来那天）：这几天不排别的生活小事。"""
        trip = st.get("travel") or {}
        return bool(trip.get("go")) and trip["go"] <= day.isoformat() <= trip["back"]

    def away(self, member: str, day: date) -> str:
        """她这天在哪儿旅行（目的地）；在家为空。公寓、空闲去处读这个。"""
        if not self.enabled():
            return ""
        st = self.states.get(member) or store.load(self.rt, member)
        trip = self.trip_on(st, day)
        return str(trip["dest"]) if trip else ""

    def _busy(self, st: dict[str, Any], day: date) -> bool:
        """这一趟还没走完（计划了还没回来）。"""
        trip = st.get("travel") or {}
        return bool(trip.get("back")) and day.isoformat() <= trip["back"]

    def _overlap(self, members: list[str], go: date, back: date) -> int:
        n = 0
        for m in members:
            trip = (self.states.get(m) or {}).get("travel") or {}
            if trip.get("go") and trip["go"] <= back.isoformat() and go.isoformat() <= trip["back"]:
                n += 1
        return n

    # --- 推进 -----------------------------------------------------------------------

    def step(
        self,
        member: str,
        day: date,
        st: dict[str, Any],
        others: list[str],
        rng: random.Random,
        friend: Callable[[], str],
        names: dict[str, str],
    ) -> list[Leg]:
        """今天要不要开始计划一趟；要就返回整趟的几步（开始那天起算）。改 st["travel"]。"""
        if not self.enabled() or self._busy(st, day):
            return []
        r = self.rules
        last = (st.get("trips") or [{}])[-1].get("back") if st.get("trips") else None
        if last and (day - date.fromisoformat(last)).days < r.cooldown_days:
            return []
        text = self.text(member)
        p = r.chance * (r.boost if store.hits(text, r.like_words) else 1.0)
        if rng.random() >= min(p, 1.0):
            return []
        dests = [x for x in r.dests if not x.months or day.month in x.months]
        if not dests:
            return []
        weights = [1.0 + 2.0 * store.hits(text, x.likes) / 3 for x in dests]
        dest = rng.choices(dests, weights)[0]
        nights = rng.randint(dest.days[0], dest.days[1])
        off = rng.randint(r.ahead_days[0], r.ahead_days[1])
        if nights <= 1:  # 短途尽量排在周末出发
            sat = (5 - (day + timedelta(days=off)).weekday()) % 7
            if sat <= 3:
                off += sat
        go, back = day + timedelta(days=off), day + timedelta(days=off + nights)
        if self._overlap([m for m in self.states if m != member], go, back) >= r.max_away:
            return []
        t = r.texts
        words = {"dest": dest.name, "name": names.get(member, member)}
        when = f"周{WEEKDAY[go.weekday()]}"
        legs = [Leg(0, "travel_plan", store.fill(t.plan, when=when, **words))]
        if off >= 1:
            legs.append(Leg(off - 1, "travel_pack", store.fill(t.pack, **words)))
        legs.append(Leg(off, "travel_go", store.fill(t.depart, **words)))
        sights = list(dest.sights)
        rng.shuffle(sights)
        for k in range(min(max(nights, 1), MAX_SIGHTS, len(sights))):
            legs.append(Leg(off + k, "travel_trip", store.fill(t.trip, sight=sights[k], **words)))
        other = friend() if others else ""
        if other:
            souvenir = rng.choice(list(dest.souvenirs))
            friend_name = names.get(other, other)
            text_back = store.fill(t.back, other=friend_name, souvenir=souvenir, **words)
        else:
            text_back = store.fill(t.back.split("，")[0], **words)
        legs.append(Leg(off + nights, "travel_back", text_back, other))
        if nights:  # 当天来回的不另记一笔「累了早睡」（一天的事别太多）
            legs.append(Leg(off + nights, "travel_rest", store.fill(t.tired, **words)))
        st["travel"] = {
            "dest": dest.name,
            "planned": day.isoformat(),
            "go": go.isoformat(),
            "back": back.isoformat(),
        }
        trips = list(st.get("trips") or [])
        trips.append(dict(st["travel"]))
        st["trips"] = trips[-KEEP_TRIPS:]
        return legs

    def view(self, st: dict[str, Any], today: date) -> dict[str, Any]:
        """给接口看：进行中的这趟和去过的地方（不带分数）。"""
        trip = st.get("travel") or {}
        now = None
        if trip.get("back") and today.isoformat() <= trip["back"]:
            now = {k: trip[k] for k in ("dest", "go", "back")}
            now["away"] = self.trip_on(st, today) is not None
        done = [
            {k: t[k] for k in ("dest", "go", "back")}
            for t in st.get("trips") or []
            if t.get("back", "") < today.isoformat()
        ]
        return {"now": now, "past": done[-5:]}
