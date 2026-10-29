"""农历和二十四节气：查预先算好的表（``lunar_table.json``，2000～2100 年，北京时间）。

表由 ``scripts/build_lunar_table.py`` 用开源库 lunar_python 生成；运行时不需要任何农历库，
不联网、不调模型。超出表的年份返回 None（不报错）。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import NamedTuple

TABLE_FILE = Path(__file__).with_name("lunar_table.json")


class LunarDate(NamedTuple):
    year: int  # 农历年（正月初一所在的公历年）
    month: int
    day: int
    leap: bool  # 闰月
    month_days: int  # 这个月有几天（29 或 30）


@cache
def _table() -> dict:
    return json.loads(TABLE_FILE.read_text(encoding="utf-8"))


@cache
def _year(y: int) -> tuple[date, list[tuple[int, bool, int]]] | None:
    row = _table()["years"].get(str(y))
    if row is None:
        return None
    months = [(m, bool(leap), days) for m, leap, days in row["months"]]
    return date.fromisoformat(row["start"]), months


def to_lunar(d: date) -> LunarDate | None:
    """公历 → 农历；超出表的范围返回 None。"""
    for y in (d.year, d.year - 1):
        info = _year(y)
        if info is None or d < info[0]:
            continue
        offset = (d - info[0]).days
        for month, leap, days in info[1]:
            if offset < days:
                return LunarDate(y, month, offset + 1, leap, days)
            offset -= days
    return None


def from_lunar(year: int, month: int, day: int, *, leap: bool = False) -> date | None:
    """农历 → 公历；没有这一天（如没有闰月、小月没有三十）返回 None。"""
    info = _year(year)
    if info is None:
        return None
    d = info[0]
    for m, is_leap, days in info[1]:
        if m == month and is_leap == leap:
            return d + timedelta(days=day - 1) if 1 <= day <= days else None
        d += timedelta(days=days)
    return None


@cache
def solar_terms(year: int) -> dict[date, str]:
    """公历 year 年的二十四节气：日期 → 名字（北京时间的日期）。"""
    terms = _table()["terms"].get(str(year), {})
    return {date.fromisoformat(f"{year}-{md}"): name for name, md in terms.items()}


def table_years() -> tuple[int, int]:
    t = _table()
    return t["first"], t["last"]
