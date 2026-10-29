"""终端时钟与公告栏的纯函数（web/js/clock.js、notice.js）：时差、夏令时、设置清理、未读。

用 node 跑，没有就跳过。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[2] / "web" / "js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="需要 node")


def run(body: str):
    script = (
        f"const C = await import({json.dumps((WEB / 'clock.js').as_uri())});"
        f"const N = await import({json.dumps((WEB / 'notice.js').as_uri())});"
        f"const out = await (async () => {{ {body} }})();"
        "process.stdout.write(JSON.stringify(out));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(res.stdout)


def test_manchester_switches_back_to_winter_time_in_late_october():
    # 2026 年英国 10 月 25 日（10 月最后一个周日）01:00 UTC 回冬令时
    out = run(
        "const a = new Date('2026-10-24T12:00:00Z'), b = new Date('2026-10-26T12:00:00Z');"
        "const L = 'Europe/London', S = 'Asia/Shanghai';"
        "return [C.offsetMinutes(a, L), C.offsetMinutes(b, L),"
        " C.diffLabel(a, S, L), C.diffLabel(b, S, L),"
        " C.timeText(C.zoneParts(b, L)), C.timeText(C.zoneParts(b, S))];"
    )
    assert out == [60, 0, "+7 小时", "+8 小时", "12:00", "20:00"]


def test_diff_labels_and_twelve_hour_text():
    out = run(
        "const t = new Date('2026-01-15T00:05:00Z');"
        "return [C.diffLabel(t, 'Asia/Kolkata', 'UTC'), C.diffLabel(t, 'America/New_York', 'UTC'),"
        " C.diffLabel(t, 'UTC', 'UTC'), C.timeText(C.zoneParts(t, 'UTC'), true),"
        " C.timeText(C.zoneParts(t, 'Asia/Shanghai'), true),"
        " C.dateText(C.zoneParts(t, 'America/New_York'))];"
    )
    assert out == ["+5 小时 30 分", "−5 小时", "本机时间", "上午 12:05", "上午 8:05", "1.14 周三"]


def test_settings_are_cleaned():
    out = run(
        "const many = C.cleanSettings({ zones: ["
        " { tz: 'Asia/Tokyo' }, { tz: 'Nowhere/City', name: 'x' },"
        " { tz: 'UTC', name: '  很长很长很长很长很长的名字 ' },"
        " { tz: 'Europe/Paris' }, { tz: 'Asia/Seoul' }], h12: true });"
        "const bad = C.cleanSettings({ zones: [{ tz: 'bad' }] });"
        "return [many, C.cleanSettings(null), bad.zones.length];"
    )
    many, default, fallback = out
    assert [z["tz"] for z in many["zones"]] == ["Asia/Tokyo", "UTC", "Europe/Paris"]
    assert many["zones"][0]["name"] == "东京" and len(many["zones"][1]["name"]) <= 8
    assert many["h12"] is True and many["date"] is True and many["diff"] is True
    assert [z["name"] for z in default["zones"]] == ["曼城", "中国"] and default["h12"] is False
    assert fallback == 2


def test_unread_versions_keep_newest_first():
    out = run(
        "const rels = [{ version: '1.3' }, { version: '1.2' }, { version: '1.1' }];"
        "return N.unreadVersions(rels, ['1.2']);"
    )
    assert out == ["1.3", "1.1"]


def test_main_zone_order_and_local_follow():
    out = run(
        "const zs = [{ tz: 'Europe/London', name: 'a' }, { tz: 'local', name: '' }, { tz: 'UTC' }];"
        "const s = C.cleanSettings({ zones: zs, main: 2 });"
        "const t = new Date('2026-01-15T12:00:00Z');"
        "return [s.main, s.zones.map((z) => z.name), C.faceOrder(s.zones, s.main).map((z) => z.tz),"
        " C.cleanSettings({ zones: zs, main: 7 }).main, C.realZone('local', 'Asia/Tokyo'),"
        " C.realZone('UTC', 'Asia/Tokyo'), C.mainDiffLabel(t, 'Europe/London', 'UTC'),"
        " C.mainDiffLabel(t, 'Asia/Shanghai', 'Europe/London')];"
    )
    assert out == [
        2,
        ["a", "本机", "协调世界时"],
        ["UTC", "Europe/London", "local"],
        0,
        "Asia/Tokyo",
        "UTC",
        "同主时区",
        "+8 小时",
    ]


def test_durations_and_timer_state():
    out = run(
        "const sw = { start: 1000, acc: 500, laps: [] };"
        "const cd = { dur: 60000, end: 61000, left: null, done: false };"
        "return [C.fmtDuration(0), C.fmtDuration(65432, true), C.fmtDuration(3723000),"
        " C.fmtDuration(-5), C.fmtLeft(299200), C.fmtLeft(0),"
        " C.swElapsed(sw, 3000), C.swElapsed({ start: null, acc: 700 }, 9e9),"
        " C.cdLeft(cd, 31000), C.cdLeft(cd, 99000), C.cdLeft({ ...cd, end: null, left: 4000 }, 0),"
        " C.cdLeft({ ...cd, end: null }, 0), C.cdLeft({ ...cd, done: true }, 0)];"
    )
    assert out == [
        "00:00",
        "01:05.43",
        "1:02:03",
        "00:00",
        "05:00",
        "00:00",
        2500,
        700,
        30000,
        0,
        4000,
        60000,
        0,
    ]


def test_timer_state_is_cleaned():
    out = run(
        "const bad = { sw: { start: 'x', acc: -3, laps: [1, 'a', 2] },"
        " cd: { dur: 0, end: 5, done: 'yes' } };"
        "return [C.cleanTimers(null), C.cleanTimers(bad)];"
    )
    empty, messy = out
    assert empty["sw"] == {"start": None, "acc": 0, "laps": []}
    assert empty["cd"] == {"dur": 300000, "end": None, "left": None, "done": False}
    assert messy["sw"] == {"start": None, "acc": 0, "laps": [1, 2]}
    assert messy["cd"] == {"dur": 300000, "end": 5, "left": None, "done": False}


def test_home_zone_parts_follow_the_chosen_zone():
    # hometz.js：主时区的墙上时间（星期 0 = 周一，和后端一致）；离线时设置也先在本页生效
    script = (
        f"const H = await import({json.dumps((WEB / 'hometz.js').as_uri())});"
        "const d = new Date('2026-01-15T00:30:00Z');"
        "const before = H.homeZone();"
        "await H.setHomeZone('Asia/Shanghai');"
        "const sh = H.homeParts(d);"
        "await H.setHomeZone('America/New_York');"
        "const ny = H.homeParts(d);"
        "const bad = await H.setHomeZone('Mars/Base');"
        "process.stdout.write(JSON.stringify({before, sh, ny, bad, zone: H.homeZone(),"
        " opt: H.tzOpt({hour: '2-digit'}),"
        " same: H.homeDay(d) === H.homeDay(new Date('2026-01-14T23:00:00Z'))}));"
    )
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    out = json.loads(res.stdout)
    assert out["before"] == "local"
    assert out["sh"] == {"y": 2026, "m": 1, "d": 15, "h": 8, "mi": 30, "wd": 3}
    assert out["ny"] == {"y": 2026, "m": 1, "d": 14, "h": 19, "mi": 30, "wd": 2}
    assert out["bad"] is False and out["zone"] == "America/New_York"
    assert out["opt"] == {"hour": "2-digit", "timeZone": "America/New_York"}
    assert out["same"] is True


def test_calendar_item_notes_and_day_labels():
    out = run(
        "return ["
        "N.itemNote({name: '圣诞节', rest: true, off: ['英国', '美国']}),"
        "N.itemNote({name: '国庆节', kind: 'holiday', off: ['中国']}),"
        "N.itemNote({name: '日本文化之日', kind: 'holiday', off: ['日本']}),"
        "N.itemNote({name: '调休上班', kind: 'workday', places: ['中国']}),"
        "N.itemNote({name: '篝火夜', kind: 'observance', places: ['英国']}),"
        "N.itemNote({name: '情人节', kind: 'observance', places: ['国际']}),"
        "N.dayLabel({date: '2026-11-05', weekday: '周四'}, 0),"
        "N.dayLabel({date: '2026-11-05', weekday: '周四'}, 1),"
        "N.dayLabel({date: '2026-11-05', weekday: '周四'}, 3)];"
    )
    assert out == [
        "成员放假",
        "中国放假",
        "",
        "中国调休上班",
        "英国",
        "",
        "今天",
        "明天",
        "11月5日 周四",
    ]
