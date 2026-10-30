"""主时区：存取、换算、夏令时；生活、日记、群聊都按它过日子。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from roundtable.core.chat import ChatHub, dm_room, life_clock
from roundtable.core.hometz import KEY, LOCAL, HomeZone, home_zone, parse, valid
from roundtable.core.service import RoundtableService, ServiceError
from roundtable.core.storage.repository import iso

from .orchestrator.conftest import Env

# 2026-01-15 00:30 UTC = 北京时间周四 08:30
AT = datetime(2026, 1, 15, 0, 30, tzinfo=UTC)


def test_parse_and_valid():
    assert parse(None) is None and parse(LOCAL) is None and parse("") is None
    assert parse("没有这个/时区") is None and parse(123) is None  # type: ignore[arg-type]
    assert parse("Asia/Shanghai").key == "Asia/Shanghai"
    assert valid(LOCAL) and valid("Europe/London") and not valid("Mars/Base")


def test_set_persists_and_follows_daylight_saving():
    env = Env()
    zone = HomeZone(env.rt.repo)
    assert zone.name() == LOCAL and zone.tz() is None
    assert zone.view(AT)["chosen"] is False
    zone.set("Europe/London")
    assert env.rt.repo.setting(KEY) == "Europe/London"
    assert HomeZone(env.rt.repo).name() == "Europe/London"  # 新建一个也读得到
    winter = zone.view(AT)
    summer = zone.view(datetime(2026, 7, 1, 12, tzinfo=UTC))
    assert winter["chosen"] and winter["offset_minutes"] == 0 and summer["offset_minutes"] == 60
    with pytest.raises(ValueError):
        zone.set("Mars/Base")
    assert zone.name() == "Europe/London"
    assert zone.set("") == LOCAL  # 清空 = 跟随电脑


def test_bad_saved_value_falls_back_to_local():
    env = Env()
    env.rt.repo.set_setting(KEY, "Mars/Base")
    assert HomeZone(env.rt.repo).name() == LOCAL


def test_runtime_shares_one_zone():
    env = Env()
    assert home_zone(env.rt) is home_zone(env.rt)


def test_life_clock_uses_the_home_zone():
    env = Env()
    env.rt.budget.now = lambda: AT
    home_zone(env.rt).set("Asia/Shanghai")
    text = life_clock(env.rt, "V1")
    assert text.startswith("1月15日 周四 08:30，你这会儿一般在")
    home_zone(env.rt).set("America/New_York")  # 前一天晚上 19:30
    assert life_clock(env.rt, "V1").startswith("1月14日 周三 19:30")


def test_life_clock_follows_what_just_happened_in_her_life():
    env = Env()
    env.rt.budget.now = lambda: AT
    topic = "时间：星期四 8 点\n地点：公寓楼天台（看日出）\n在场：小一"
    env.rt.repo.add_life_event(iso(AT - timedelta(minutes=30)), "solo", ["V1"], topic=topic)
    assert life_clock(env.rt, "V1").endswith("你刚才在公寓楼天台（看日出）")
    assert "一般在" in life_clock(env.rt, "V2")  # 别人的事不算


async def test_dm_prompt_knows_the_time_but_work_group_does_not():
    env = Env()
    env.rt.budget.now = lambda: AT
    home_zone(env.rt).set("Asia/Shanghai")
    hub = ChatHub(env.rt)
    dm = hub.room(dm_room("V2"))
    msg, speakers = dm.post("在干嘛")
    await dm.replies(msg["id"], speakers)
    assert "1月15日 周四 08:30" in env.fake.calls[-1].messages[0].content
    # 工作群不带生活作息（工作与生活分开）
    work = hub.room("work")
    msg, _ = work.post("@阿三 进度怎么样")
    await work.replies(msg["id"], work.pick("@阿三 进度怎么样"))
    assert "08:30" not in env.fake.calls[-1].messages[0].content


async def test_service_ticks_life_in_the_home_zone():
    env = Env()
    env.rt.budget.now = lambda: AT
    svc = RoundtableService(env.rt)
    assert svc.home_tz()["zone"] == LOCAL
    assert svc.set_home_tz("Asia/Shanghai")["offset_minutes"] == 8 * 60
    seen: list[datetime] = []

    async def tick(now):
        seen.append(now)
        return {}

    svc.life.tick = tick
    await svc.life_tick()
    assert seen[0].hour == 8 and seen[0].utcoffset().total_seconds() == 8 * 3600
    with pytest.raises(ServiceError):
        svc.set_home_tz("Mars/Base")
