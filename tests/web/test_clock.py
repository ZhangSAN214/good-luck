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
