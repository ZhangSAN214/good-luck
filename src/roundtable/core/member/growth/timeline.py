"""成长时间线（P6）：每周反思的结果都记下来——改了什么、为什么、引用了哪些经历、评审几分、
被拦下的原因、跳过的原因；改动可以一条一条撤回。另有每周的本地备份（留 ``backups`` 份）。

- 存在 ``app_settings`` 的 ``member_growth:<成员>`` 键（一位成员一份 JSON，不加迁移），
  **只有成长插件写**（测试守卫）。用户可以一键清空。
- 条目 ``kind``：``change`` 写进了档案 / ``rejected`` 被程序或评审拦下 / ``skip`` 这周没改
  （经历太少、没想改、额度不够……）/ ``revert`` 你撤回了某一条。
- **撤回**：按当前档案算出反向的一步（性格分减回去、加的那项去掉、去掉的那项加回来），
  以用户身份写成档案的新版本；那一条标成已撤回，不再计入每月格数和用户影响占比。
- **备份**：每周反思完，把全部成员的档案和成长数据写成 ``<周>.json``，只留最近几份。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from roundtable.core.member.growth import habits
from roundtable.core.member.growth.gate import ADD, CORE, REMOVE, SCORE, Ledger, Verdict
from roundtable.core.member.profile import USER, ProfileError, ProfileStore

CHANGE, REJECTED, SKIP, REVERT = "change", "rejected", "skip", "revert"


def empty() -> dict[str, Any]:
    # counts / cursor：习惯计数和读到哪个事件；reports：月度报告；skills_at：上份报告时的熟练度
    return {
        "timeline": [],
        "next_id": 1,
        "week": None,
        "counts": {},
        "cursor": None,
        "reports": {},
        "skills_at": {},
    }


class GrowthLog:
    def __init__(self, rt: Any) -> None:
        self.rt = rt
        self.profiles = ProfileStore(rt)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.growth

    def load(self, member: str) -> dict[str, Any]:
        return empty() | (self.rt.repo.growth_data(member) or {})

    def save(self, member: str, data: dict[str, Any]) -> None:
        data["timeline"] = data["timeline"][-self.rules.timeline_keep :]
        self.rt.repo.save_growth_data(member, data)

    def entries(self, member: str) -> list[dict[str, Any]]:
        """时间线（新的在前）。"""
        return list(reversed(self.load(member)["timeline"]))

    def done_week(self, member: str) -> str | None:
        return self.load(member).get("week")

    def add(self, member: str, entries: list[dict[str, Any]], *, week: str | None = None) -> None:
        data = self.load(member)
        now = self.rt.repo.clock()
        for e in entries:
            data["timeline"].append({"id": data["next_id"], "at": now, **e})
            data["next_id"] += 1
        if week is not None:
            data["week"] = week
        self.save(member, data)

    @staticmethod
    def entry(week: str, v: Verdict, *, kind: str, version: int | None = None) -> dict[str, Any]:
        p = v.proposal
        return {
            "week": week,
            "kind": kind,
            "op": p.op,
            "path": p.path,
            "value": p.value,
            "delta": p.delta,
            "old": v.old,
            "new": v.new,
            "why": p.why,
            "quote": p.quote,
            "evidence": v.evidence,
            "user": v.user,
            "score": v.score,
            "reason": v.reason,
            "clash": v.clash,
            "version": version,
            "reverted": False,
        }

    def ledger(self, member: str, today: date) -> Ledger:
        """这个月已改的格数、最近几周和入职以来没撤回的改动（及其中受用户影响的条数）、
        成长让每项性格一共移动了几格。"""
        month = today.isoformat()[:7]
        horizon = today.toordinal() - 7 * self.rules.share_window_weeks
        out = Ledger()
        for e in self.load(member)["timeline"]:
            if e["kind"] != CHANGE or e.get("reverted"):
                continue
            at = datetime.fromisoformat(e["at"]).date()
            if e["op"] == SCORE and e["at"][:7] == month:
                out.core_steps += 1
            out.life_total += 1
            out.life_user += int(bool(e.get("user")))
            if e["op"] == SCORE:  # 成长让每项性格一共移动了几格（离人物卡多远）
                trait = e["path"].rsplit(".", 1)[-1]
                out.moved[trait] = out.moved.get(trait, 0) + int(e.get("delta") or 0)
            if at.toordinal() >= horizon:
                out.total += 1
                out.user += int(bool(e.get("user")))
        return out

    def revert(self, member: str, entry_id: int) -> dict[str, Any]:
        """撤回一条成长改动（以用户身份存成档案的新版本）。返回更新后的那一条。"""
        data = self.load(member)
        e = next((x for x in data["timeline"] if x["id"] == entry_id), None)
        if e is None or e["kind"] != CHANGE:
            raise ProfileError("没有这条成长改动")
        if e.get("reverted"):
            raise ProfileError("这条已经撤回过了")
        patch = self._inverse(member, e)
        version = None
        if patch:
            p = self.profiles.change(
                member, patch, author=USER, reason=f"撤回成长（{e['week']}）：{e['path']}"
            )
            version = p.version
        e["reverted"] = True
        data["timeline"].append(
            {
                "id": data["next_id"],
                "at": self.rt.repo.clock(),
                "week": e["week"],
                "kind": REVERT,
                "of": e["id"],
                "path": e["path"],
                "version": version,
            }
        )
        data["next_id"] += 1
        self.save(member, data)
        return e

    def _inverse(self, member: str, e: dict[str, Any]) -> dict[str, dict[str, Any]]:
        cur = self.profiles.current(member).data
        sec, _, rest = e["path"].partition(".")
        key, _, trait = rest.partition(".")
        if e["op"] == SCORE:
            scores = copy.deepcopy((cur.get("personality") or {}).get("五项") or {})
            if trait not in scores:
                return {}
            scores[trait] = min(5, max(1, int(scores[trait]) - int(e["delta"])))
            return {"personality": {"五项": scores}}
        items = (cur.get(sec) or {}).get(key)
        items = list(items) if isinstance(items, list) else []
        if e["op"] == ADD and e["value"] in items:
            return {sec: {key: [x for x in items if x != e["value"]] or None}}
        if e["op"] == REMOVE and e["value"] not in items:
            return {sec: {key: [*items, e["value"]]}}
        return {}

    def count(
        self,
        member: str,
        events: list[dict[str, Any]],
        until: str,
        tz: Any,
        place_name: Callable[[str], str] = str,
    ) -> int:
        """把游标以后、until 以前这位成员的事件累加进习惯计数，游标移到 until。"""
        data = self.load(member)
        cursor = data.get("cursor") or ""
        mine = [e for e in events if e["member"] == member and cursor <= e["at"] < until]
        n = habits.count(data, mine, tz, place_name)
        data["cursor"] = until
        self.save(member, data)
        return n

    def save_report(
        self, member: str, report: dict[str, Any], skills: list[dict[str, Any]]
    ) -> None:
        """存一份月度报告，并记下这时的熟练度（下个月比升了几级）。"""
        data = self.load(member)
        data["reports"][report["month"]] = report
        for old in sorted(data["reports"])[: -self.rules.report.keep]:
            data["reports"].pop(old)
        data["skills_at"] = {s["domain"]: s["level"] for s in skills}
        self.save(member, data)

    def clear(self, member: str | None = None) -> int:
        return self.rt.repo.clear_growth_data(member)

    # --- 每周本地备份 ------------------------------------------------------------------

    def backup(self, folder: Path | None, week: str, members: list[str]) -> Path | None:
        """把全部成员的档案和成长数据写成 <周>.json，只留最近 backups 份。没有目录时不写。"""
        if folder is None:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        out: dict[str, Any] = {"week": week, "at": self.rt.repo.clock(), "members": {}}
        for m in members:
            p = self.profiles.current(m)
            out["members"][m] = {"version": p.version, "profile": p.data, "growth": self.load(m)}
        path = folder / f"{week}.json"
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        for old in sorted(folder.glob("*-W*.json"))[: -self.rules.backups]:
            old.unlink(missing_ok=True)
        return path


__all__ = ["CHANGE", "CORE", "REJECTED", "REVERT", "SKIP", "GrowthLog", "empty"]
