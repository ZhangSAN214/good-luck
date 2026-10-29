"""日历底座：算出某一天是什么日子，每天把「今天」和「快到了」发到成员事件总线。

- **法定假日**：开源库 holidays（离线）按 ``calendar.rest``（成员休息日，默认英格兰）和
  ``calendar.cultures``（也过、但不放假的国家，如中国、美国、日本）算；中国的调休补班也有。
- **农历节日、二十四节气**：查预先算好的表（``lunar.py``）。
- **其他节日**：``calendar.observances``（情人节、万圣节、篝火夜、夏令时切换……）。
- **重要日期**：成员生日（``personas.yaml`` 的 ``birthdays``）、入职纪念日（成员第一次在圆桌说话
  那天，自动算）、你自己加的纪念日（只存本机 ``app_settings``，可改可删）。
- 同一天名字相同的合并成一条（如元旦：英国放假，中国、美国、日本也放假）。
- **只给数据**：成员怎么过节由生活引擎 / 成员系统决定；不调模型、不联网。所有成员看到的日历
  完全相同（中立）；工作模式不挂载（``tests/core/member/test_mode.py`` 守卫）。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from functools import cache
from typing import Any

from dateutil.easter import easter

from roundtable.core.almanac.lunar import from_lunar, solar_terms, to_lunar
from roundtable.core.member.bus import Event

log = logging.getLogger(__name__)

USER_DATES_KEY = "calendar.user_dates"
LAST_DAY_KEY = "calendar.last_day"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_ZH = "一二三四五六日"
LUNAR_MONTHS = ("正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊")
LUNAR_DAYS_TENS = ("初", "十", "廿", "三")
DIGITS = "一二三四五六七八九十"
OBSERVED = " (observed)"
SHIFTED = re.compile(r"^休息日（由 .+ 调休）$")  # holidays 的中国调休放假

# 一天里各类条目的先后（休息日最前）
ORDER = ("holiday", "birthday", "joined", "user", "lunar", "observance", "term", "workday")
# 「快到了」不提醒的种类（节气、补班天天有人提没意思）
NO_UPCOMING = frozenset({"term", "workday"})


@dataclass(frozen=True)
class DayItem:
    date: date
    kind: str  # holiday / workday / lunar / term / observance / birthday / joined / user
    name: str
    places: tuple[str, ...] = ()  # 哪里的节日
    off: tuple[str, ...] = ()  # 哪些地方放假（法定假日）
    rest: bool = False  # 成员的休息日（休息地区的法定假日）
    member: str | None = None  # 成员生日 / 入职纪念日是谁的
    years: int | None = None  # 第几周年
    id: str | None = None  # 你加的纪念日的编号

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        d["places"], d["off"] = list(self.places), list(self.off)
        return {k: v for k, v in d.items() if v not in (None, (), [])}


def lunar_text(d: date) -> str:
    """农历的写法，如「九月十九」「闰六月初一」；超出表的范围返回空串。"""
    lu = to_lunar(d)
    if lu is None:
        return ""
    if lu.day == 10:
        day = "初十"
    elif lu.day == 20:
        day = "二十"
    elif lu.day == 30:
        day = "三十"
    else:
        day = LUNAR_DAYS_TENS[lu.day // 10] + DIGITS[lu.day % 10 - 1]
    return ("闰" if lu.leap else "") + LUNAR_MONTHS[lu.month - 1] + "月" + day


def rule_date(rule: str, year: int) -> date | None:
    """节日规则 → 当年的日期（规则写法见 ``CalendarObservance``）。"""
    if rule.startswith("easter"):
        return easter(year) + timedelta(days=int(rule[6:]))
    parts = rule.split("-")
    if len(parts) == 2:
        try:
            return date(year, int(parts[0]), int(parts[1]))
        except ValueError:
            return None  # 02-29 在平年没有
    nth, wd, month = parts[0], WEEKDAYS.index(parts[1]), int(parts[2])
    if nth == "last":
        d = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
        return d - timedelta(days=(d.weekday() - wd) % 7)
    first = date(year, month, 1)
    d = first + timedelta(days=(wd - first.weekday()) % 7 + 7 * (int(nth) - 1))
    return d if d.month == month else None


def _norm(name: str) -> str:
    """合并同名节日用：去掉「农历」前缀和「节」字尾（农历除夕 = 除夕，清明节 = 清明）。"""
    return name.removeprefix("农历").removesuffix("节")


@cache
def _holidays(country: str, subdiv: str | None, language: str | None, year: int) -> Any:
    import holidays  # 只在用到时加载

    return holidays.country_holidays(country, subdiv=subdiv, years=year, language=language)


class Almanac:
    """members()：现在有哪些成员（有 ``id``、``name``）；只用来写「谁的生日」。"""

    def __init__(self, rt: Any, members: Callable[[], Iterable[Any]]) -> None:
        self.rt = rt
        self.members = members

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.calendar

    # --- 某一天 ------------------------------------------------------------------

    def items(self, d: date) -> list[DayItem]:
        """这一天的全部条目（已合并同名、按种类排好）。"""
        raw = [
            *self._region_items(d),
            *self._lunar_items(d),
            *self._observance_items(d),
            *self._people_items(d),
        ]
        merged: dict[tuple[str, str | None], DayItem] = {}
        for it in raw:
            key = (_norm(it.name), it.member or it.id)
            old = merged.get(key)
            if old is None:
                merged[key] = it
                continue
            # 法定假日和农历节日 / 节气 / 节日同名时，保留后者的种类；农历节日用配置里的名字
            kind = old.kind if old.kind != "holiday" else it.kind
            name = it.name if it.kind == "lunar" else old.name
            merged[key] = replace(
                old,
                kind=kind,
                name=name,
                places=tuple(dict.fromkeys(old.places + it.places)),
                off=tuple(dict.fromkeys(old.off + it.off)),
                rest=old.rest or it.rest,
            )
        return sorted(merged.values(), key=lambda i: (not i.rest, ORDER.index(i.kind)))

    def is_rest_day(self, d: date) -> bool:
        """成员的休息日：周末，或休息地区的法定假日。"""
        return d.weekday() >= 5 or any(i.rest for i in self.items(d))

    def day(self, d: date) -> dict[str, Any]:
        items = self.items(d)
        return {
            "date": d.isoformat(),
            "weekday": "周" + WEEKDAY_ZH[d.weekday()],
            "lunar": lunar_text(d),
            "rest": d.weekday() >= 5 or any(i.rest for i in items),
            "items": [i.to_dict() for i in items],
        }

    def span(self, start: date, days: int) -> list[dict[str, Any]]:
        return [self.day(start + timedelta(days=n)) for n in range(days)]

    # --- 来源 --------------------------------------------------------------------

    def _regions(self) -> list[tuple[Any, bool]]:
        r = self.rules
        return ([(r.rest, True)] if r.rest else []) + [(c, False) for c in r.cultures]

    def _region_items(self, d: date) -> list[DayItem]:
        out: list[DayItem] = []
        for region, is_rest in self._regions():
            try:
                table = _holidays(region.country, region.subdiv, region.language, d.year)
            except Exception:  # noqa: BLE001 - 库不认识的国家只记日志，不影响别的
                log.warning("日历：holidays 不支持 %s/%s", region.country, region.subdiv)
                continue
            for name in table.get_list(d):
                label = (region.label,)
                out.append(DayItem(d, "holiday", self._name(name), label, label, rest=is_rest))
            if d in getattr(table, "weekend_workdays", ()):
                out.append(DayItem(d, "workday", "调休上班", (region.label,)))
        return out

    def _name(self, name: str) -> str:
        if SHIFTED.match(name):
            return "调休放假"
        names = self.rules.names
        if name.endswith(OBSERVED):
            base = name.removesuffix(OBSERVED)
            return names.get(base, base) + "（补假）"
        return names.get(name, name)

    def _lunar_items(self, d: date) -> list[DayItem]:
        out: list[DayItem] = []
        lu = to_lunar(d) if self.rules.lunar_festivals else None
        if lu is not None and not lu.leap:
            for f in self.rules.lunar_festivals:
                month, day = f.lunar.split("-")
                last = day == "last" and lu.day == lu.month_days
                if int(month) == lu.month and (last or (day != "last" and int(day) == lu.day)):
                    out.append(DayItem(d, "lunar", f.name))
        if self.rules.solar_terms and (term := solar_terms(d.year).get(d)):
            out.append(DayItem(d, "term", term))
        return out

    def _observance_items(self, d: date) -> list[DayItem]:
        return [
            DayItem(d, "observance", o.name, (o.label,))
            for o in self.rules.observances
            if rule_date(o.rule, d.year) == d
        ]

    def _people_items(self, d: date) -> list[DayItem]:
        out: list[DayItem] = []
        birthdays = self.rt.config.personas.birthdays
        joined = self.rt.repo.first_chat_message_by_author()
        md = d.strftime("%m-%d")
        for m in self.members():
            bday = birthdays.get(m.id)
            if bday == md or (md == "02-28" and _leap_birthday(bday, d)):
                out.append(DayItem(d, "birthday", f"{m.name}的生日", member=m.id))
            first = joined.get(m.id)
            if first:
                j = datetime.fromisoformat(first).astimezone().date()
                if j.strftime("%m-%d") == md and d.year > j.year:
                    name = f"{m.name}入职 {d.year - j.year} 周年"
                    out.append(DayItem(d, "joined", name, member=m.id, years=d.year - j.year))
        for u in self.user_dates():
            hit = _user_date_on(u, d)
            if hit is not None:
                out.append(DayItem(d, "user", u["name"], ("你",), years=hit, id=u["id"]))
        return out

    # --- 你加的纪念日（只存本机） ---------------------------------------------------

    def user_dates(self) -> list[dict[str, Any]]:
        raw = self.rt.repo.setting(USER_DATES_KEY)
        try:
            return list(json.loads(raw)) if raw else []
        except ValueError:
            return []

    def add_user_date(self, name: str, when: str, *, lunar: bool = False) -> dict[str, Any]:
        """加一个每年都过的纪念日。when：YYYY-MM-DD（会算第几周年）或 MM-DD；lunar 为农历。"""
        name = name.strip()
        if not name or len(name) > 30:
            raise ValueError("纪念日名字要 1～30 个字")
        _parse_when(when, lunar=lunar)
        dates = self.user_dates()
        if len(dates) >= self.rules.max_user_dates:
            raise ValueError(f"纪念日最多 {self.rules.max_user_dates} 个")
        row = {"id": uuid.uuid4().hex[:8], "name": name, "date": when, "lunar": lunar}
        self._save(dates + [row])
        return row

    def remove_user_date(self, date_id: str) -> bool:
        dates = self.user_dates()
        keep = [u for u in dates if u["id"] != date_id]
        self._save(keep)
        return len(keep) != len(dates)

    def _save(self, dates: list[dict[str, Any]]) -> None:
        self.rt.repo.set_setting(USER_DATES_KEY, json.dumps(dates, ensure_ascii=False))

    # --- 事件 --------------------------------------------------------------------

    def tick(self, now: datetime, publish: Callable[[Any], Any]) -> int:
        """每天第一次调用时发事件：``calendar.day``（今天）和 ``calendar.upcoming``（N 天后）。

        关机期间错过的日子不补发（过去的节日没意义）。返回发了几个事件。"""
        today = now.date()
        if not self.rules.enabled or self.rt.repo.setting(LAST_DAY_KEY) == today.isoformat():
            return 0
        self.rt.repo.set_setting(LAST_DAY_KEY, today.isoformat())
        publish(Event("calendar.day", now, None, self.day(today)))
        n = 1
        if self.rules.upcoming_days:
            ahead = today + timedelta(days=self.rules.upcoming_days)
            for it in self.items(ahead):
                if it.kind in NO_UPCOMING:
                    continue
                data = it.to_dict() | {"in_days": self.rules.upcoming_days}
                publish(Event("calendar.upcoming", now, it.member, data))
                n += 1
        return n


def _leap_birthday(md: str | None, d: date) -> bool:
    """02-29 生日在平年按 02-28 过。"""
    return md == "02-29" and not _is_leap(d.year)


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _parse_when(when: str, *, lunar: bool) -> tuple[int | None, int, int]:
    parts = when.split("-")
    try:
        if len(parts) == 3:
            y, m, dd = (int(p) for p in parts)
        elif len(parts) == 2:
            y, (m, dd) = None, (int(p) for p in parts)
        else:
            raise ValueError
        if lunar:
            if not (1 <= m <= 12 and 1 <= dd <= 30):
                raise ValueError
        else:
            date(2000, m, dd)  # 闰年，02-29 也合法
        if y is not None and not 1900 <= y <= 2100:
            raise ValueError
    except ValueError:
        raise ValueError("日期要写成 YYYY-MM-DD 或 MM-DD") from None
    return y, m, dd


def _user_date_on(u: dict[str, Any], d: date) -> int | None:
    """你加的纪念日在 d 这天吗？是的话返回第几周年（没写年份时为 0），否则 None。"""
    try:
        y, m, dd = _parse_when(u["date"], lunar=bool(u.get("lunar")))
    except (ValueError, KeyError):
        return None
    if u.get("lunar"):
        lu = to_lunar(d)
        if lu is None or lu.leap or lu.month != m:
            return None
        # 小月没有三十：在这个月最后一天过
        if not (lu.day == dd or (dd == 30 and lu.month_days == 29 and lu.day == 29)):
            return None
        on_year = lu.year
        if from_lunar(lu.year, m, min(dd, lu.month_days)) != d:
            return None
    else:
        if (d.month, d.day) != (m, dd) and not (
            (m, dd) == (2, 29) and (d.month, d.day) == (2, 28) and not _is_leap(d.year)
        ):
            return None
        on_year = d.year
    if y is None:
        return 0
    return on_year - y if on_year >= y else None
