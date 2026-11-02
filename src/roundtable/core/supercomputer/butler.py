"""超级电脑的管家：定时活、排队、自治闸门、日报、哈希链审计（docs/SUPERCOMPUTER.md「管家」）。

借的是「管家骨架」的思路（调度 + 自治闸门 + 日报 + 审计链），代码是圆桌自己写的。

- **定时活**：你建的「每天 / 每周几 / 某一天 几点干什么」。到点交给闸门：
  放行就排进队，不放行就进「等你确认」。
  电脑关着错过了点，开机后 ``catch_up_hours`` 小时之内补跑一次（只补一次），再晚记为「错过」。
- **排队**：超级电脑一次只干一件；排进队的活（你批准的、你点了「排队」的、闸门放行的定时活）
  按先后接着干。
- **闸门**：见 ``gate.py``。你亲手发起的都直接干；定时活到点才看风险和每天自己跑的上限。
- **日报**：每天一份，程序按记录汇总（不调模型、不花钱），存在 ``data/super/reports/``。
- **审计**：交活、批准、拒绝、改次数、叫醒、建删定时活、闸门判断、每件活的结局
  都记进 ``audit.jsonl``，前后用哈希串起来；日报顺带记下当天的锚点。

不会自己找活干：只调度你建好的定时活和你排进队的活。不导入 dsh，dsh 不在时照常运行。
日期、几点都按本机时区（``now`` 交进来的时间带时区）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import secrets
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from roundtable.core.config.schema import SuperButler
from roundtable.core.supercomputer.audit import AuditLog
from roundtable.core.supercomputer.engine import Supercomputer
from roundtable.core.supercomputer.gate import AUTONOMY_NAME, RISK_NAME, decide

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
WEEK = "一二三四五六日"
SOURCE_NAME = {"manual": "你交的", "schedule": "定时", "queue": "排队"}
STATUS_NAME = {
    "splitting": "拆分中",
    "working": "在干",
    "merging": "合并中",
    "done": "完成",
    "failed": "失败",
    "cancelled": "叫停",
}
MAX_TEXT = 4000
log = logging.getLogger(__name__)


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _parse(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def when_text(s: dict[str, Any]) -> str:
    if s.get("once"):
        return f"{s['once']} {s['at']}"
    days = s.get("days") or []
    if not days or len(days) == 7:
        return f"每天 {s['at']}"
    if sorted(days) == [0, 1, 2, 3, 4]:
        return f"工作日 {s['at']}"
    return "每周" + "、".join(WEEK[d] for d in sorted(days)) + f" {s['at']}"


def last_due(s: dict[str, Any], now: datetime) -> datetime | None:
    """这条定时活最近一次「该干」的时间（不晚于 now）；还没到过就是 None。"""
    hh, mm = (int(x) for x in s["at"].split(":"))
    tz = now.tzinfo
    if s.get("once"):
        t = datetime.combine(date.fromisoformat(s["once"]), time(hh, mm), tz)
        return t if t <= now else None
    days = set(s.get("days") or range(7))
    for back in range(8):
        d = now.date() - timedelta(days=back)
        t = datetime.combine(d, time(hh, mm), tz)
        if t <= now and d.weekday() in days:
            return t
    return None


class Butler:
    def __init__(
        self,
        sc: Supercomputer,
        rules: SuperButler,
        *,
        root: Path | None,
        now: Callable[[], datetime],
    ) -> None:
        self.sc = sc
        self.rules = rules
        self.root = root
        self.now = now
        self.audit = AuditLog(root / "audit.jsonl" if root else None, now=now)
        self.path = root / "butler.json" if root else None
        self.data: dict[str, Any] = {
            "schedules": [],
            "pending": [],
            "queue": [],
            "auto": {"day": "", "count": 0},
            "anchors": {},
            "reported": "",
        }
        self._load()
        sc.on_finish = self._finished

    # --- 存取 -------------------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            for key, default in self.data.items():
                if isinstance(loaded.get(key), type(default)):
                    self.data[key] = loaded[key]

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def _today(self) -> str:
        return self.now().date().isoformat()

    def _auto_used(self) -> int:
        auto = self.data["auto"]
        return int(auto["count"]) if auto.get("day") == self._today() else 0

    def _find(self, key: str, item_id: str) -> dict[str, Any]:
        for item in self.data[key]:
            if item["id"] == item_id:
                return item
        names = {"schedules": "定时活", "pending": "待确认的活", "queue": "排队的活"}
        raise LookupError(f"没有这件{names[key]}：{item_id}")

    # --- 状态 -------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        gate = self.rules.gate
        schedules = []
        now = self.now()
        for s in self.data["schedules"]:
            schedules.append({**s, "when": when_text(s), "next": self._next(s, now)})
        return {
            "enabled": self.rules.enabled,
            "autonomy": gate.autonomy,
            "autonomy_text": AUTONOMY_NAME[gate.autonomy],
            "auto_used": self._auto_used(),
            "auto_max": gate.max_auto_per_day,
            "schedules": schedules,
            "pending": list(self.data["pending"]),
            "queue": list(self.data["queue"]),
            "audit": {"count": self.audit.count, "head": self.audit.head},
        }

    def _next(self, s: dict[str, Any], now: datetime) -> str | None:
        if not s.get("enabled", True):
            return None
        hh, mm = (int(x) for x in s["at"].split(":"))
        if s.get("once"):
            t = datetime.combine(date.fromisoformat(s["once"]), time(hh, mm), now.tzinfo)
            return _iso(t) if t > now else None
        days = set(s.get("days") or range(7))
        for ahead in range(8):
            d = now.date() + timedelta(days=ahead)
            t = datetime.combine(d, time(hh, mm), now.tzinfo)
            if t > now and d.weekday() in days:
                return _iso(t)
        return None

    # --- 你发起的 -----------------------------------------------------------------

    def submit(self, text: str, *, queue: bool = False) -> dict[str, Any]:
        """你在面板上交活。超级电脑在忙时：queue=True 就排进队，否则照旧报「在忙」。"""
        text = text.strip()
        if not text:
            raise ValueError("要交的活是空的")
        v = decide(text, self.rules.gate, source="manual", auto_used=0)
        if self.sc.busy and queue:
            return self._enqueue(text, source="manual", ref=None, risk=v.risk, why="你点了排队")
        task = self.sc.submit(text)
        self.audit.append("submit", task=task["id"], source="manual", text=text, risk=v.risk)
        return task

    def _enqueue(
        self, text: str, *, source: str, ref: str | None, risk: str, why: str
    ) -> dict[str, Any]:
        if len(self.data["queue"]) >= self.rules.queue_max:
            raise ValueError(f"队里已经有 {self.rules.queue_max} 件了，先等前面的干完")
        item = {
            "id": f"q{secrets.token_hex(3)}",
            "text": text[:MAX_TEXT],
            "source": source,
            "ref": ref,
            "risk": risk,
            "why": why,
            "at": _iso(self.now()),
            "error": None,
        }
        self.data["queue"].append(item)
        self._save()
        self.audit.append("queue", id=item["id"], source=source, ref=ref, text=text, why=why)
        return item

    def unqueue(self, item_id: str) -> None:
        item = self._find("queue", item_id)
        self.data["queue"].remove(item)
        self._save()
        self.audit.append("unqueue", id=item_id, text=item["text"])

    def add_schedule(
        self, text: str, at: str, *, days: list[int] | None = None, once: str | None = None
    ) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise ValueError("定时活的内容是空的")
        if not HHMM.match(at or ""):
            raise ValueError("时间要写成 HH:MM，比如 09:30")
        days = sorted(set(days or []))
        if any(not 0 <= d <= 6 for d in days):
            raise ValueError("星期要在 0（周一）～ 6（周日）之间")
        if once is not None:
            try:
                when = datetime.combine(
                    date.fromisoformat(once), time(*map(int, at.split(":"))), self.now().tzinfo
                )
            except ValueError:
                raise ValueError("日期要写成 YYYY-MM-DD") from None
            if when <= self.now():
                raise ValueError("这个时间已经过了")
            days = []
        if len(self.data["schedules"]) >= self.rules.max_schedules:
            raise ValueError(f"定时活最多 {self.rules.max_schedules} 条")
        v = decide(text, self.rules.gate, source="schedule", auto_used=0)
        item = {
            "id": f"s{secrets.token_hex(3)}",
            "text": text[:MAX_TEXT],
            "at": at,
            "days": days,
            "once": once,
            "enabled": True,
            "risk": v.risk,
            "created_at": _iso(self.now()),
            "last_fired": _iso(self.now()),  # 建之前的点不补
            "last_result": None,
        }
        self.data["schedules"].append(item)
        self._save()
        self.audit.append(
            "schedule_add", id=item["id"], text=text, when=when_text(item), risk=v.risk
        )
        return item

    def remove_schedule(self, item_id: str) -> None:
        item = self._find("schedules", item_id)
        self.data["schedules"].remove(item)
        self._save()
        self.audit.append("schedule_remove", id=item_id, text=item["text"])

    def toggle_schedule(self, item_id: str, enabled: bool) -> dict[str, Any]:
        item = self._find("schedules", item_id)
        item["enabled"] = bool(enabled)
        if enabled:
            item["last_fired"] = _iso(self.now())  # 停着时错过的点不补
        self._save()
        self.audit.append("schedule_toggle", id=item_id, enabled=bool(enabled))
        return item

    def run_schedule(self, item_id: str) -> dict[str, Any]:
        """「现在跑」：你点的，算你发起；在忙就排进队。"""
        item = self._find("schedules", item_id)
        v = decide(item["text"], self.rules.gate, source="manual", auto_used=0)
        self.audit.append("schedule_run", id=item_id, risk=v.risk)
        if self.sc.busy:
            return self._enqueue(
                item["text"], source="schedule", ref=item_id, risk=v.risk, why="你点了现在跑"
            )
        return self.sc.submit(item["text"], source="schedule", ref=item_id)

    def approve(self, item_id: str) -> dict[str, Any]:
        item = self._find("pending", item_id)
        self.data["pending"].remove(item)
        self._save()
        self.audit.append("approve", id=item_id, ref=item.get("ref"), text=item["text"])
        out = self._enqueue(
            item["text"], source="schedule", ref=item.get("ref"), risk=item["risk"], why="你批准了"
        )
        self.drain()
        return out

    def reject(self, item_id: str) -> None:
        item = self._find("pending", item_id)
        self.data["pending"].remove(item)
        self._save()
        self.audit.append("reject", id=item_id, ref=item.get("ref"), text=item["text"])
        self._mark(item.get("ref"), "你没批")

    def set_limit(self, worker: str, value: int) -> None:
        self.sc.set_limit(worker, value)
        self.audit.append("limit", worker=worker, limit=value)

    def wake(self, worker: str) -> None:
        self.sc.clear_cooldown(worker)
        self.audit.append("wake", worker=worker)

    async def cancel(self) -> None:
        task = self.sc.task
        if self.sc.busy and task is not None:
            self.audit.append("cancel", task=task["id"])
        await self.sc.cancel()

    # --- 定时 -------------------------------------------------------------------

    def tick(self) -> dict[str, int]:
        """看一次表：补写日报 → 到点的定时活过闸门 → 空闲就从队里接一件。返回这次做了什么。"""
        out = {"fired": 0, "asked": 0, "missed": 0, "started": 0, "reports": 0}
        if not self.rules.enabled:
            return out
        out["reports"] = self.roll_reports()
        now = self.now()
        for s in list(self.data["schedules"]):
            if not s.get("enabled", True):
                continue
            due = last_due(s, now)
            fired = _parse(s.get("last_fired"))
            if due is None or (fired is not None and fired >= due):
                continue
            s["last_fired"] = _iso(now)
            if s.get("once"):
                s["enabled"] = False
            if now - due > timedelta(hours=self.rules.catch_up_hours):
                s["last_result"] = f"{due:%m-%d %H:%M} 电脑没开，错过了"
                self._save()
                self.audit.append("missed", id=s["id"], due=_iso(due))
                out["missed"] += 1
                continue
            late = now - due > timedelta(minutes=10)
            v = decide(s["text"], self.rules.gate, source="schedule", auto_used=self._auto_used())
            self.audit.append("gate", id=s["id"], due=_iso(due), late=late, **v.as_dict())
            if v.run:
                try:
                    self._enqueue(s["text"], source="schedule", ref=s["id"], risk=v.risk, why=v.why)
                except ValueError as exc:
                    s["last_result"] = str(exc)
                else:
                    auto = self.data["auto"]
                    if auto.get("day") != self._today():
                        auto["day"], auto["count"] = self._today(), 0
                    auto["count"] += 1
                    s["last_result"] = "补跑：" + v.why if late else v.why
                    out["fired"] += 1
            else:
                self.data["pending"].append(
                    {
                        "id": f"p{secrets.token_hex(3)}",
                        "ref": s["id"],
                        "text": s["text"],
                        "risk": v.risk,
                        "risk_text": RISK_NAME[v.risk],
                        "why": v.why,
                        "at": _iso(now),
                        "due": _iso(due),
                    }
                )
                s["last_result"] = "等你确认"
                out["asked"] += 1
            self._save()
        out["started"] = self.drain()
        return out

    def drain(self) -> int:
        """超级电脑空着就从队里接一件。接不了（没人能干、超级电脑不可用）就留在队里，原因写上。"""
        if self.sc.busy or not self.data["queue"]:
            return 0
        item = self.data["queue"][0]
        try:
            task = self.sc.submit(item["text"], source=item["source"], ref=item.get("ref"))
        except (ValueError, RuntimeError) as exc:
            if item.get("error") != str(exc):
                item["error"] = str(exc)
                self._save()
                self.audit.append("queue_wait", id=item["id"], why=str(exc))
            return 0
        self.data["queue"].pop(0)
        self._save()
        self.audit.append(
            "submit",
            task=task["id"],
            source=item["source"],
            ref=item.get("ref"),
            text=item["text"],
            risk=item.get("risk"),
            queue=item["id"],
        )
        return 1

    def _mark(self, ref: str | None, text: str) -> None:
        if not ref:
            return
        for s in self.data["schedules"]:
            if s["id"] == ref:
                s["last_result"] = text
                self._save()

    def _finished(self, task: dict[str, Any]) -> None:
        workers = sorted(
            {
                str(a.get("worker"))
                for rec in [task.get("split"), task.get("merge"), *task.get("subtasks", [])]
                if isinstance(rec, dict)
                for a in rec.get("attempts", [])
            }
        )
        self.audit.append(
            "finish",
            task=task["id"],
            status=task.get("status"),
            source=task.get("source"),
            workers=workers,
            subtasks=len(task.get("subtasks") or []),
        )
        status = STATUS_NAME.get(str(task.get("status")), str(task.get("status")))
        self._mark(task.get("ref"), f"{task['id']}：{status}")
        if self.data["queue"]:  # 这件活收尾后马上从队里接下一件（不等下次看表）
            with contextlib.suppress(RuntimeError):
                asyncio.get_running_loop().call_soon(self._drain_quietly)

    def _drain_quietly(self) -> None:
        try:
            self.drain()
        except Exception:  # noqa: BLE001 - 接不上就等下次看表
            log.exception("超级电脑管家：从队里接活出错")

    # --- 日报 -------------------------------------------------------------------

    def _bounds(self, day: date) -> tuple[datetime, datetime]:
        tz = self.now().tzinfo
        start = datetime.combine(day, time(0, 0), tz)
        return start, start + timedelta(days=1)

    def _tasks(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        if self.sc.root is None:
            return []
        base = self.sc.root / "tasks"
        if not base.is_dir():
            return []
        out = []
        for d in sorted(base.iterdir()):
            try:
                t = json.loads((d / "task.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            at = _parse(t.get("created_at"))
            if at is not None and start <= at < end:
                out.append(t)
        return out

    def report(self, day: str | None = None) -> dict[str, Any]:
        """某天的日报（默认今天，现算）。程序按记录汇总，不调模型。"""
        try:
            d = date.fromisoformat(day) if day else self.now().date()
        except ValueError:
            raise ValueError("日期要写成 YYYY-MM-DD") from None
        start, end = self._bounds(d)
        tasks = self._tasks(start, end)
        entries = self.audit.since(start, end)
        tally = {"done": 0, "failed": 0, "cancelled": 0, "running": 0}
        rows, per = [], {}
        labels = {w.id: w.label for w in self.sc.rules.workers}
        for t in tasks:
            st = str(t.get("status"))
            tally[st if st in tally else "running"] += 1
            began, ended = _parse(t.get("created_at")), _parse(t.get("finished_at"))
            mins = f"{(ended - began).total_seconds() / 60:.0f} 分钟" if began and ended else "—"
            tz = self.now().tzinfo
            rows.append(
                f"| {began.astimezone(tz):%H:%M} | {SOURCE_NAME.get(t.get('source'), '你交的')} "
                f"| {_cell(t.get('text'))} | {STATUS_NAME.get(st, st)} | {mins} |"
                if began
                else ""
            )
            for rec in [t.get("split"), t.get("merge"), *(t.get("subtasks") or [])]:
                if not isinstance(rec, dict):
                    continue
                for a in rec.get("attempts", []):
                    p = per.setdefault(
                        str(a.get("worker")), {"tries": 0, "ok": 0, "failed": 0, "limited": 0}
                    )
                    p["tries"] += 1
                    if a.get("ok"):
                        p["ok"] += 1
                    elif "额度" in str(a.get("why", "")):
                        p["limited"] += 1
                    else:
                        p["failed"] += 1
        gates = [e for e in entries if e.get("act") in {"gate", "approve", "reject", "missed"}]
        auto = sum(1 for e in gates if e["act"] == "gate" and e["data"].get("run"))
        asked = sum(1 for e in gates if e["act"] == "gate" and not e["data"].get("run"))
        check = self.audit.verify(self.data["anchors"])
        summary = (
            f"交了 {len(tasks)} 件：完成 {tally['done']}、失败 {tally['failed']}、"
            f"叫停 {tally['cancelled']}"
            + (f"、还在干 {tally['running']}" if tally["running"] else "")
            + f"。定时活自己跑 {auto} 件，等你确认 {asked} 件。"
            + (
                f"审计链完好（共 {check['count']} 条）。"
                if check["ok"]
                else f"审计链第 {check['broken_at']} 条对不上：{check['why']}。"
            )
        )
        lines = [f"# 超级电脑日报 {d.isoformat()}", "", summary, ""]
        if rows:
            lines += [
                "## 每件活",
                "",
                "| 时间 | 来源 | 活 | 结果 | 用时 |",
                "|---|---|---|---|---|",
            ]
            lines += [r for r in rows if r] + [""]
        if per:
            lines += [
                "## 每位助手",
                "",
                "| 助手 | 派了几次 | 成功 | 失败 | 撞额度 |",
                "|---|---|---|---|---|",
            ]
            for wid, p in sorted(per.items()):
                lines.append(
                    f"| {_cell(labels.get(wid, wid))} | {p['tries']} | {p['ok']} "
                    f"| {p['failed']} | {p['limited']} |"
                )
            lines.append("")
        if gates:
            lines += ["## 闸门", ""]
            tz = self.now().tzinfo
            for e in gates:
                at = _parse(e.get("at"))
                hm = f"{at.astimezone(tz):%H:%M}" if at else "—"
                lines.append(f"- {hm} {_gate_line(e, self.data['schedules'])}")
            lines.append("")
        if d == self.now().date() and self.data["pending"]:
            lines += ["## 还在等你确认", ""]
            lines += [f"- {_cell(p['text'])}（{p['why']}）" for p in self.data["pending"]] + [""]
        lines += [
            "## 审计",
            "",
            f"今天记了 {len(entries)} 条；链上一共 {check['count']} 条，"
            f"最新一条的 hash 开头 {check['head'][:12]}。",
        ]
        return {
            "day": d.isoformat(),
            "summary": summary,
            "tasks": len(tasks),
            **tally,
            "auto": auto,
            "asked": asked,
            "workers": per,
            "audit": check,
            "markdown": "\n".join(lines) + "\n",
        }

    def roll_reports(self) -> int:
        """把还没写的前几天日报写进 reports/，并记下每天的锚点。返回写了几份。"""
        if self.root is None:
            return 0
        today = self.now().date()
        last = self.data.get("reported") or ""
        first = today - timedelta(days=self.rules.report_days)
        if last:
            with contextlib.suppress(ValueError):
                first = max(first, date.fromisoformat(last) + timedelta(days=1))
        wrote = 0
        d = first
        while d < today:
            start, end = self._bounds(d)
            entries = self.audit.since(start, end)
            if entries:
                tail = entries[-1]
                self.data["anchors"][d.isoformat()] = {"n": tail["n"], "hash": tail["hash"]}
                folder = self.root / "reports"
                folder.mkdir(parents=True, exist_ok=True)
                rep = self.report(d.isoformat())
                (folder / f"{d.isoformat()}.md").write_text(rep["markdown"], encoding="utf-8")
                wrote += 1
            self.data["reported"] = d.isoformat()
            d += timedelta(days=1)
        if wrote or last != self.data.get("reported"):
            self._save()
        return wrote

    def reports(self) -> list[str]:
        """写好的日报有哪些天（新的在前）。"""
        if self.root is None or not (self.root / "reports").is_dir():
            return []
        return sorted((p.stem for p in (self.root / "reports").glob("*.md")), reverse=True)

    def audit_view(self, limit: int = 30) -> dict[str, Any]:
        entries = self.audit.entries()
        return {
            "check": self.audit.verify(self.data["anchors"]),
            "anchors": dict(self.data["anchors"]),
            "recent": entries[-limit:][::-1],
        }


def _cell(text: Any) -> str:
    s = str(text or "").replace("\n", " ").replace("|", "｜").strip()
    return s if len(s) <= 40 else s[:39] + "…"


def _gate_line(e: dict[str, Any], schedules: list[dict[str, Any]]) -> str:
    data = e.get("data", {})
    texts = {s["id"]: s["text"] for s in schedules}
    what = _cell(data.get("text") or texts.get(data.get("ref") or data.get("id"), "")) or "定时活"
    act = e.get("act")
    if act == "gate":
        return f"「{what}」{data.get('why', '')}"
    if act == "approve":
        return f"你批准了「{what}」"
    if act == "reject":
        return f"你没批「{what}」"
    return f"「{what}」到点时电脑没开，错过了"
