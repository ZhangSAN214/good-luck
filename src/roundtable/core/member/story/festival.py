"""节日纪念日（L2，docs/LIFE_STORY.md「节日纪念日」）：读日历（``core/almanac``），不另算日子，不调用模型。

- **节日**（``chat.life.story.festival.festivals``）：日历上的节日名字含 ``match`` 里的字就算。
  提前 ``prep_days`` 天有一位去准备（买月饼、挑南瓜）；当天在场的大家一起过。``origin`` 是来历词：
  档案里提到这些词的成员把它当家乡的节日——由她领头，当天还会有点想家（``homesick``）。
- **成员生日**：前一天有人偷偷准备礼物（礼物从寿星档案的「最爱」里挑，没有就从 ``gifts`` 挑），
  当天大家给她过；送礼的人和礼物由寿星和年份决定，前一天和当天对得上。
- **入住周年**：翻翻刚来时的照片。
- 一天只排一次（``member_life_festival`` 记最后排到哪天），排进领头那位的日程，到点发生；
  这些事不受每天上限，但会占掉她当天的名额（那天就少一件别的小事）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

from roundtable.core.member.story import store

PREFIX = "fest_"
KEY = "member_life_festival"
TAGS = {
    "fest_prep": [],
    "fest_day": ["story.good"],
    "fest_homesick": ["story.homesick"],
    "fest_bday_prep": [],
    "fest_bday": ["story.good"],
    "fest_joined": ["story.good"],
}
KINDS = frozenset({"holiday", "lunar", "observance"})


@dataclass(frozen=True)
class Plan:
    lead: str
    members: list[str]
    story: str
    text: str
    hours: tuple[int, int]
    place: str


def tags_of(story: str) -> list[str]:
    return list(TAGS.get(story, []))


class Festivals:
    def __init__(self, rt: Any, text: Any) -> None:
        self.rt = rt
        self.text = text  # 成员 → 档案全文（StoryEngine.text）

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.life.story.festival

    def enabled(self) -> bool:
        return (
            self.rt.config.roundtable.chat.life.story.enabled
            and self.rules.enabled
            and self.rt.config.roundtable.calendar.enabled
        )

    def due(self, day: date) -> bool:
        """这一天还没排过（一天只排一次）。"""
        last = self.rt.repo.setting(KEY) or ""
        return self.enabled() and day.isoformat() > last

    def plan(self, day: date, names: dict[str, str]) -> list[Plan]:
        from roundtable.core.almanac import Almanac

        self.rt.repo.set_setting(KEY, day.isoformat())
        people = [SimpleNamespace(id=m, name=n) for m, n in sorted(names.items())]
        cal = Almanac(self.rt, lambda: people)
        rng = random.Random(f"festival:{day.isoformat()}")
        members = sorted(names)
        out: list[Plan] = []
        r = self.rules
        today = cal.items(day)
        for spec in r.festivals:
            local = [m for m in members if spec.origin and store.hits(self.text(m), spec.origin)]
            hours = (spec.hours[0], spec.hours[1])
            ahead = cal.items(day + timedelta(days=spec.prep_days)) if spec.prep else []
            if spec.prep_days and (item := self._match(spec, ahead)):
                lead = rng.choice(local or members)
                text = store.fill(spec.prep, name=names[lead], fest=item.name)
                out.append(Plan(lead, [lead], "fest_prep", text, (10, 20), "out"))
            if item := self._match(spec, today):
                lead = rng.choice(local or members)
                text = store.fill(spec.day, name=names[lead], fest=item.name)
                out.append(
                    Plan(
                        lead,
                        [lead, *[m for m in members if m != lead]],
                        "fest_day",
                        text,
                        hours,
                        spec.place,
                    )
                )
                for m in local if spec.homesick else []:
                    text = store.fill(spec.homesick, name=names[m], fest=item.name)
                    out.append(Plan(m, [m], "fest_homesick", text, (21, 24), "dorm"))
        for item in cal.items(day + timedelta(days=1)):
            if item.kind == "birthday" and item.member in names and len(members) > 1:
                giver, gift = self._gift(item.member, members, day.year)
                text = store.fill(r.birthday_prep, name=names[item.member], other=names[giver])
                out.append(Plan(giver, [giver], "fest_bday_prep", text, (10, 21), "out"))
        for item in today:
            if item.kind == "birthday" and item.member in names:
                m = item.member
                giver, gift = self._gift(m, members, day.year)
                other = names.get(giver, "")
                text = store.fill(r.birthday, name=names[m], other=other, gift=gift)
                out.append(
                    Plan(
                        m,
                        [m, *[x for x in members if x != m]],
                        "fest_bday",
                        text,
                        (19, 22),
                        "lounge",
                    )
                )
            elif item.kind == "joined" and item.member in names and item.years:
                text = store.fill(r.joined, name=names[item.member], years=str(item.years))
                out.append(Plan(item.member, [item.member], "fest_joined", text, (21, 24), "dorm"))
        return out

    @staticmethod
    def _match(spec: Any, items: list[Any]) -> Any:
        return next(
            (i for i in items if i.kind in KINDS and any(w in i.name for w in spec.match)), None
        )

    def _gift(self, member: str, members: list[str], year: int) -> tuple[str, str]:
        """送礼的人和礼物：寿星 + 年份决定（前一天准备的和当天送的对得上）。"""
        rng = random.Random(f"gift:{member}:{year}")
        others = [m for m in members if m != member]
        giver = rng.choice(others) if others else member
        fav = store.favorites(self.rt, member)
        if fav:
            return giver, store.fill(self.rules.like_gift, like=rng.choice(fav))
        return giver, rng.choice(list(self.rules.gifts))
