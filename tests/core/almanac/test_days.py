"""日历底座：各国假日与调休、农历节日、节气、节日规则、合并同名、生日与纪念日、事件、中立。"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace as N

import pytest

from roundtable.core.almanac import Almanac
from roundtable.core.almanac.days import USER_DATES_KEY, lunar_text, rule_date

from ..orchestrator.conftest import Env

MEMBERS = [N(id="OpenAI", name="小白"), N(id="Moonshot AI", name="小K")]


def _cal(env: Env | None = None, members=MEMBERS) -> Almanac:
    return Almanac((env or Env()).rt, lambda: members)


def _names(cal: Almanac, d: date) -> list[str]:
    return [i.name for i in cal.items(d)]


def test_rest_days_follow_england():
    cal = _cal()
    good_friday = cal.items(date(2026, 4, 3))
    assert [(i.name, i.rest) for i in good_friday] == [("耶稣受难日", True)]
    assert cal.is_rest_day(date(2026, 4, 3)) and cal.is_rest_day(date(2026, 10, 31))  # 周六
    assert not cal.is_rest_day(date(2026, 10, 1))  # 中国国庆：成员住英国，不放假
    assert "国庆节" in _names(cal, date(2026, 10, 1))


def test_same_holiday_merges_across_countries():
    (newyear,) = _cal().items(date(2026, 1, 1))
    assert newyear.name == "元旦" and newyear.rest
    assert set(newyear.off) == {"英国", "中国", "美国", "日本"}
    (xmas,) = _cal().items(date(2026, 12, 25))
    assert xmas.name == "圣诞节" and set(xmas.off) == {"英国", "美国"}


def test_china_shift_days_and_lunar_festivals():
    cal = _cal()
    items = {i.name: i for i in cal.items(date(2026, 2, 16))}
    assert items["除夕"].kind == "lunar" and items["除夕"].off == ("中国",)  # 农历除夕合并成一条
    assert "美国总统日" in items
    assert [i.name for i in cal.items(date(2026, 2, 14)) if i.kind == "workday"] == ["调休上班"]
    assert "调休放假" in _names(cal, date(2026, 5, 5))
    assert "七夕" in _names(cal, date(2026, 8, 19)) and "立夏" in _names(cal, date(2026, 5, 5))


def test_observance_rules():
    assert rule_date("easter-21", 2026) == date(2026, 3, 15)  # 英国母亲节
    assert rule_date("2-sun-05", 2026) == date(2026, 5, 10)
    assert rule_date("last-sun-10", 2026) == date(2026, 10, 25)
    assert rule_date("last-sun-12", 2026) == date(2026, 12, 27)
    assert rule_date("02-29", 2026) is None and rule_date("5-sun-02", 2026) is None
    cal = _cal()
    assert "篝火夜" in _names(cal, date(2026, 11, 5))
    assert "夏令时结束（拨慢一小时）" in _names(cal, date(2026, 10, 25))
    assert "感恩节" in _names(cal, date(2026, 11, 26))


def test_lunar_text():
    assert lunar_text(date(2026, 10, 29)) == "九月二十"
    assert lunar_text(date(2026, 2, 17)) == "正月初一"
    assert lunar_text(date(2025, 7, 25)) == "闰六月初一"
    assert lunar_text(date(1800, 1, 1)) == ""


def test_member_birthdays_and_joined_anniversary():
    env = Env()
    env.rt.repo.first_chat_message_by_author = lambda: {"Moonshot AI": "2025-10-09T09:00:00+00:00"}
    cal = _cal(env)
    bday = [i for i in cal.items(date(2026, 11, 30)) if i.kind == "birthday"]
    assert [(i.name, i.member) for i in bday] == [("小白的生日", "OpenAI")]
    kinds = {i.kind: i for i in cal.items(date(2026, 10, 9))}
    assert kinds["birthday"].member == "Moonshot AI"
    assert kinds["joined"].years == 1 and kinds["joined"].name == "小K入职 1 周年"
    assert "joined" not in {i.kind for i in cal.items(date(2025, 10, 9))}  # 入职当天不算周年


def test_user_dates_add_remove_and_lunar():
    env = Env()
    cal = _cal(env)
    a = cal.add_user_date("来曼城的日子", "2025-09-20")
    b = cal.add_user_date("妈妈生日", "08-15", lunar=True)
    c = cal.add_user_date("闰年纪念", "02-29")
    on = {i.name: i for i in cal.items(date(2026, 9, 20))}
    assert on["来曼城的日子"].years == 1 and on["来曼城的日子"].id == a["id"]
    assert "妈妈生日" in _names(cal, date(2026, 9, 25))  # 农历八月十五
    assert "闰年纪念" in _names(cal, date(2026, 2, 28))  # 平年在 2 月 28 日过
    assert json.loads(env.rt.repo.setting(USER_DATES_KEY))[0]["name"] == "来曼城的日子"
    assert cal.remove_user_date(b["id"]) and not cal.remove_user_date(b["id"])
    assert [u["id"] for u in cal.user_dates()] == [a["id"], c["id"]]
    for bad in [("", "09-20"), ("x", "2025-13-01"), ("x", "9/20"), ("x", "13-01")]:
        with pytest.raises(ValueError):
            cal.add_user_date(*bad)


def test_tick_publishes_today_once_and_upcoming():
    env = Env()
    cal = _cal(env)
    sent = []
    now = datetime(2026, 11, 2, 8, tzinfo=UTC)  # 3 天后是篝火夜
    assert cal.tick(now, sent.append) >= 2
    kinds = [e.kind for e in sent]
    assert kinds[0] == "calendar.day" and sent[0].data["date"] == "2026-11-02"
    up = [e.data["name"] for e in sent if e.kind == "calendar.upcoming"]
    assert "篝火夜" in up
    assert cal.tick(now.replace(hour=20), sent.append) == 0  # 一天只发一次
    assert cal.tick(datetime(2026, 11, 3, 8, tzinfo=UTC), sent.append) >= 1


def test_birthday_upcoming_is_addressed_to_member():
    sent = []
    _cal().tick(datetime(2026, 11, 27, 8, tzinfo=UTC), sent.append)
    (bday,) = [e for e in sent if e.kind == "calendar.upcoming" and e.data["kind"] == "birthday"]
    assert bday.member == "OpenAI" and bday.data["in_days"] == 3


def test_disabled_publishes_nothing():
    env = Env()
    env.rt.config = env.rt.config.model_copy(
        update={
            "roundtable": env.rt.config.roundtable.model_copy(
                update={
                    "calendar": env.rt.config.roundtable.calendar.model_copy(
                        update={"enabled": False}
                    )
                }
            )
        }
    )
    assert _cal(env).tick(datetime(2026, 11, 2, tzinfo=UTC), lambda e: None) == 0


def test_same_calendar_for_every_member():
    """中立：节日与谁是哪个模型无关；只有生日 / 入职条目带成员。"""
    cal = _cal()
    for d in (date(2026, 1, 1), date(2026, 2, 17), date(2026, 10, 31)):
        assert all(i.member is None for i in cal.items(d))


def test_config_validation():
    from pydantic import ValidationError

    from roundtable.core.config.schema import CalendarObservance, LunarFestival

    CalendarObservance(name="ok", rule="last-sun-03")
    for bad in ("3-14", "easter", "6-sun-05", "last-xyz-10"):
        with pytest.raises(ValidationError):
            CalendarObservance(name="x", rule=bad)
    with pytest.raises(ValidationError):
        LunarFestival(name="x", lunar="13")
    personas = Env().rt.config.personas
    with pytest.raises(ValidationError):
        type(personas).model_validate(personas.model_dump() | {"birthdays": {"X": "13-01"}})
