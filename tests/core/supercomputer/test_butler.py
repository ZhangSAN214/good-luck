"""管家：定时活到点过闸门、排队接着干、错过补跑一次、等你确认、日报、审计都记上。"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from roundtable.core.config.schema import SuperButler, SuperGate
from roundtable.core.supercomputer import Butler, Supercomputer
from roundtable.core.supercomputer.butler import last_due, when_text

from .test_engine import Agents, Launcher, render, rules

BJ = timezone(timedelta(hours=8))
T0 = datetime(2026, 11, 2, 8, 0, tzinfo=BJ)  # 周一早上 8 点（本机时间）


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t


def make(tmp_path, agents=None, **gate):
    clock = Clock()
    lau = Launcher()
    sc = Supercomputer(
        rules(),
        root=tmp_path / "super",
        render=render,
        launchers={"command": lau, "dsh": lau},
        now=clock,
        runner=agents or Agents(),
        seed=7,
    )
    b = Butler(sc, SuperButler(gate=SuperGate(**gate)), root=sc.root, now=clock)
    return b, sc, clock


def acts(b):
    return [e["act"] for e in b.audit.entries()]


def test_last_due_and_when():
    s = {"at": "09:30", "days": [0, 2]}  # 周一、周三
    assert last_due(s, T0) == datetime(2026, 10, 28, 9, 30, tzinfo=BJ)  # 上周三
    assert last_due(s, T0.replace(hour=10)) == T0.replace(hour=9, minute=30)
    assert when_text(s) == "每周一、三 09:30"
    assert when_text({"at": "07:00", "days": [0, 1, 2, 3, 4]}) == "工作日 07:00"
    assert when_text({"at": "07:00", "days": []}) == "每天 07:00"
    once = {"at": "09:00", "once": "2026-11-03"}
    assert last_due(once, T0) is None and when_text(once) == "2026-11-03 09:00"


async def test_schedule_fires_low_risk_and_runs(tmp_path):
    b, sc, clock = make(tmp_path)
    s = b.add_schedule("调研一下竞品，总结成表格", "09:00")
    assert s["risk"] == "low" and b.status()["schedules"][0]["next"].startswith("2026-11-02T09:00")
    assert b.tick()["fired"] == 0  # 还没到点
    clock.t = T0.replace(hour=9, minute=1)
    out = b.tick()
    assert out["fired"] == 1 and out["started"] == 1
    assert sc.task["source"] == "schedule" and sc.task["ref"] == s["id"]
    await sc.wait()
    assert sc.task["status"] == "done"
    assert b.tick()["fired"] == 0  # 同一个点不跑第二次
    assert b.data["schedules"][0]["last_result"].endswith("完成")
    assert acts(b) == ["schedule_add", "gate", "queue", "submit", "finish"]
    assert b.status()["auto_used"] == 1


async def test_medium_risk_waits_for_you(tmp_path):
    b, sc, clock = make(tmp_path)
    s = b.add_schedule("给接口加分页，补测试", "09:00")
    clock.t = T0.replace(hour=9)
    out = b.tick()
    assert out == {"fired": 0, "asked": 1, "missed": 0, "started": 0, "reports": 0}
    assert not sc.busy
    pending = b.status()["pending"]
    assert pending[0]["ref"] == s["id"] and "只放行" in pending[0]["why"]
    b.approve(pending[0]["id"])  # 你批了：排进队，空着就马上开干
    assert sc.busy and sc.task["source"] == "schedule" and not b.status()["pending"]
    await sc.wait()
    with pytest.raises(LookupError):
        b.approve(pending[0]["id"])
    # 第二天又到点：这次不批
    clock.t += timedelta(days=1)
    b.tick()
    p = b.status()["pending"][0]
    b.reject(p["id"])
    assert b.data["schedules"][0]["last_result"] == "你没批"
    assert "approve" in acts(b) and "reject" in acts(b)


async def test_daily_auto_cap(tmp_path):
    b, sc, clock = make(tmp_path, max_auto_per_day=1)
    b.add_schedule("调研 A", "09:00")
    b.add_schedule("调研 B", "09:00")
    clock.t = T0.replace(hour=9)
    out = b.tick()
    assert out["fired"] == 1 and out["asked"] == 1
    assert "用完" in b.status()["pending"][0]["why"]
    await sc.wait()


async def test_missed_while_off_catches_up_once(tmp_path):
    b, sc, clock = make(tmp_path)
    b.add_schedule("调研 A", "09:00")
    clock.t = T0.replace(hour=15)  # 电脑 9 点没开，下午才开：12 小时内，补跑一次
    out = b.tick()
    assert out["fired"] == 1 and b.data["schedules"][0]["last_result"].startswith("补跑")
    await sc.wait()
    assert b.tick()["fired"] == 0
    clock.t += timedelta(days=2)  # 关了两天：前天的点太久了，昨天的点也过了 12 小时以上
    clock.t = clock.t.replace(hour=22)
    out = b.tick()
    assert out["missed"] == 1 and "错过" in b.data["schedules"][0]["last_result"]


async def test_queue_when_busy(tmp_path):
    b, sc, clock = make(tmp_path, agents=Agents(delay=0.2))
    b.submit("第一件")
    with pytest.raises(RuntimeError):
        b.submit("第二件")
    q = b.submit("第二件", queue=True)
    assert q["source"] == "manual" and b.status()["queue"][0]["text"] == "第二件"
    q3 = b.submit("第三件", queue=True)
    b.unqueue(q3["id"])
    assert b.tick()["started"] == 0  # 还在忙
    await sc.wait()
    await asyncio.sleep(0)
    assert sc.task["text"] == "第二件"  # 前一件收尾就马上接着干，不等看表
    assert not b.status()["queue"]
    await sc.wait()
    # 接不了（超级电脑不可用）：留在队里，写上原因
    sc.rules = sc.rules.model_copy(update={"enabled": False})
    q = b._enqueue("第四件", source="manual", ref=None, risk="low", why="测试")
    assert b.drain() == 0 and b.status()["queue"][0]["error"].startswith("超级电脑在配置里关掉")
    b.unqueue(q["id"])


async def test_once_and_toggle_and_run_now(tmp_path):
    b, sc, clock = make(tmp_path, autonomy="off")
    with pytest.raises(ValueError, match="已经过了"):
        b.add_schedule("调研", "07:00", once="2026-11-02")
    with pytest.raises(ValueError, match="HH:MM"):
        b.add_schedule("调研", "9点")
    s = b.add_schedule("调研", "09:00", once="2026-11-02")
    b.toggle_schedule(s["id"], False)
    clock.t = T0.replace(hour=9)
    assert b.tick()["asked"] == 0  # 停着不跑
    b.toggle_schedule(s["id"], True)  # 停着时错过的点不补
    assert b.tick()["asked"] == 0
    t = b.run_schedule(s["id"])  # 你点「现在跑」：不看闸门
    assert t["source"] == "schedule"
    await sc.wait()
    b.remove_schedule(s["id"])
    assert not b.status()["schedules"]
    with pytest.raises(LookupError):
        b.remove_schedule(s["id"])


async def test_report_and_anchor(tmp_path):
    b, sc, clock = make(tmp_path, agents=Agents(limited={"k"}))
    b.add_schedule("调研竞品", "09:00")
    b.add_schedule("改登录页", "09:00")
    clock.t = T0.replace(hour=9)
    b.tick()
    await sc.wait()
    rep = b.report()
    assert rep["tasks"] == 1 and rep["done"] == 1 and rep["auto"] == 1 and rep["asked"] == 1
    md = rep["markdown"]
    assert "# 超级电脑日报 2026-11-02" in md and "| 定时 |" in md and "调研竞品" in md
    assert "## 每位助手" in md and "## 还在等你确认" in md and "审计链完好" in md
    assert rep["workers"]["k"]["limited"] >= 1  # 撞额度单独数
    # 第二天看表：写好昨天的日报，记下锚点
    clock.t += timedelta(days=1)
    assert b.tick()["reports"] == 1
    saved = (tmp_path / "super" / "reports" / "2026-11-02.md").read_text(encoding="utf-8")
    assert "调研竞品" in saved and b.reports() == ["2026-11-02"]
    anchor = b.data["anchors"]["2026-11-02"]
    day_end = datetime(2026, 11, 3, tzinfo=BJ)
    last = [e for e in b.audit.entries() if datetime.fromisoformat(e["at"]) < day_end][-1]
    assert anchor == {"n": last["n"], "hash": last["hash"]}
    await sc.wait()
    assert b.tick()["reports"] == 0
    # 整条链重写：日报锚点对不上
    path = tmp_path / "super" / "audit.jsonl"
    rows = path.read_text(encoding="utf-8").splitlines()
    e = json.loads(rows[0])
    e["data"]["text"] = "别的"
    rows[0] = json.dumps(e, ensure_ascii=False)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    view = b.audit_view()
    assert not view["check"]["ok"] and view["check"]["broken_at"] == 1
    assert "对不上" in b.report("2026-11-02")["summary"]
    with pytest.raises(ValueError):
        b.report("昨天")


async def test_persisted_across_restart(tmp_path):
    b, sc, clock = make(tmp_path)
    b.add_schedule("改登录页", "09:00")
    clock.t = T0.replace(hour=9)
    b.tick()
    again = Butler(sc, b.rules, root=sc.root, now=clock)
    assert again.status()["pending"][0]["text"] == "改登录页"
    assert again.audit.count == b.audit.count


async def test_medium_autonomy(tmp_path):
    b, sc, clock = make(tmp_path, autonomy="medium")
    b.add_schedule("给接口加分页", "09:00")
    b.add_schedule("部署到服务器", "09:00")
    clock.t = T0.replace(hour=9)
    out = b.tick()
    assert out["fired"] == 1 and out["asked"] == 1
    assert b.status()["queue"] == [] and sc.task["text"] == "给接口加分页"
    assert b.status()["pending"][0]["risk"] == "high"
    await sc.wait()
