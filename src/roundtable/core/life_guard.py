"""收得住：成员互聊的收尾、每日条数上限、熔断和每周自检（docs/CHAT.md G9）。

全部由代码判断，不调用模型、不花钱：

- **收尾**：一段互聊里新写出的一句，和这段前面的话或这个会话最近的互聊太像
  （字符二元组相似度 ≥ ``stale_similarity``），说明没有新内容，这句不发，这段到此结束。
  每句最多 ``max_chars`` 字。
- **每日条数上限**：成员之间每天（UTC）最多说 ``daily_messages`` 句（含分享），到了就只记一行。
- **熔断**：生活最近连续 ``max_failures`` 次调用出错，或预算告急（生活日上限 / 聊天月预算用到
  ``budget_alarm``，或总预算不够），当天的生活停掉（事件照记，不再调用模型），第二天自动恢复。
- **每周自检**：每周 ``report_weekday`` 之后第一次推进时，给上一周（周一到周日，UTC）写一份报告：
  花了多少、谁说得多、重复了什么、出错和熔断。存在 ``life_reports``，网页和接口可看。

收尾、上限、熔断都记进 ``life_guard_log``（只增不改），自检时汇总。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.budget.guard import day_window as utc_day_window
from roundtable.core.budget.guard import month_window
from roundtable.core.moments import similarity
from roundtable.core.storage.repository import iso

ERRORS = "连续 "  # 因为连续出错停掉的原因以它开头（记忆整理这时也不跑）
HALT_KEY = "life.halt"  # {"day": "2026-10-29", "reason": "…"}：当天停掉
STALE, CAP, HALT = "stale", "cap", "halt"


def week_key(moment: datetime) -> str:
    """ISO 周，如 2026-W43。"""
    year, week, _ = moment.isocalendar()
    return f"{year}-W{week:02d}"


def week_window(moment: datetime) -> tuple[datetime, datetime]:
    """moment 所在 ISO 周的 [周一 0 点, 下周一 0 点)（UTC）。"""
    start, _ = utc_day_window(moment)
    start -= timedelta(days=start.weekday())
    return start, start + timedelta(days=7)


class LifeGuard:
    """生活引擎的护栏。steps：算作生活的调用步骤；spent_today：生活今天花了多少。"""

    def __init__(self, rt: Any, steps: tuple[str, ...]) -> None:
        self.rt = rt
        self.steps = steps

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.life.guard

    @property
    def life_rules(self) -> Any:
        return self.rt.config.roundtable.chat.life

    def _day(self) -> tuple[datetime, datetime]:
        return utc_day_window(self.rt.budget.now())

    # --- 收尾 --------------------------------------------------------------------

    def clip(self, text: str) -> str:
        limit = self.rules.max_chars if self.rules.enabled else len(text)
        if len(text) > limit:
            return text[:limit].rstrip() + "…"
        return text

    def recent_lines(self, room: str) -> list[str]:
        if not self.rules.enabled or self.rules.recent_messages == 0:
            return []
        rows = self.rt.repo.recent_life_messages(room, self.rules.recent_messages)
        return [r["text"] for r in rows]

    def is_stale(self, text: str, earlier: list[str]) -> bool:
        """text 和 earlier 里任何一句太像：没有新内容。"""
        if not self.rules.enabled:
            return False
        return any(similarity(text, e) >= self.rules.stale_similarity for e in earlier)

    def note_stale(self, member: str, text: str) -> None:
        self.rt.repo.add_guard_log(STALE, text[:80], member=member)

    # --- 每日条数上限 ----------------------------------------------------------------

    def messages_today(self) -> int:
        start, end = self._day()
        return self.rt.repo.count_life_messages(iso(start), iso(end))

    def room_left(self) -> int:
        """今天成员之间还能说几句。"""
        if not self.rules.enabled:
            return 1 << 30
        return max(0, self.rules.daily_messages - self.messages_today())

    def note_cap(self) -> None:
        """到了上限：每天只记一次。"""
        start, end = self._day()
        if not any(r["kind"] == CAP for r in self.rt.repo.guard_log_between(iso(start), iso(end))):
            self.rt.repo.add_guard_log(CAP, f"成员之间今天已经说了 {self.rules.daily_messages} 句")

    # --- 熔断 --------------------------------------------------------------------

    def halted(self) -> str | None:
        """今天停掉的原因；没停时为 None。"""
        if not self.rules.enabled:
            return None
        raw = self.rt.repo.setting(HALT_KEY)
        if not raw:
            return None
        info = json.loads(raw)
        today = self._day()[0].date().isoformat()
        return info["reason"] if info.get("day") == today else None

    def halt(self, reason: str) -> None:
        today = self._day()[0].date().isoformat()
        self.rt.repo.set_setting(HALT_KEY, json.dumps({"day": today, "reason": reason}))
        self.rt.repo.add_guard_log(HALT, reason)

    def why_halt(self, spent_today: float) -> str | None:
        """该不该当天停掉：连续出错、预算告急。"""
        rules, life = self.rules, self.life_rules
        start, end = self._day()
        recent = self.rt.repo.recent_call_errors("chat", self.steps, start, end, rules.max_failures)
        if len(recent) >= rules.max_failures and all(recent):
            return f"{ERRORS}{rules.max_failures} 次调用出错"
        if life.daily_usd > 0 and spent_today >= life.daily_usd * rules.budget_alarm:
            return f"生活今天已花 ${spent_today:.3f}，接近每日上限 ${life.daily_usd:.2f}"
        limit = self.rt.config.roundtable.chat.budget.monthly_usd
        m_start, m_end = month_window(self.rt.budget.now())
        chat = self.rt.repo.spent_in_workflow("chat", m_start, m_end)
        if chat >= limit * rules.budget_alarm:
            return f"聊天本月已花 ${chat:.2f}，接近月预算 ${limit:.2f}"
        if not self.rt.budget.check(life.estimate_usd).allowed:
            return "总预算不够了"
        return None

    def check(self, spent_today: float) -> str | None:
        """检查并在需要时停掉当天的生活。返回停掉的原因。"""
        if not self.rules.enabled:
            return None
        reason = self.halted()
        if reason:
            return reason
        reason = self.why_halt(spent_today)
        if reason:
            self.halt(reason)
        return reason

    # --- 每周自检 ------------------------------------------------------------------

    def report(self, start: datetime, end: datetime, names: dict[str, str]) -> dict[str, Any]:
        """[start, end) 的自检：花费、谁说得多、重复、异常。"""
        repo, rules = self.rt.repo, self.rules
        steps = repo.calls_by_step("chat", start, end)
        life_cost = sum(s["cost_usd"] for s in steps if s["step"] in self.steps)
        total_cost = sum(s["cost_usd"] for s in steps)
        days = max(1.0, (end - start).total_seconds() / 86400)

        speakers: dict[str, dict[str, Any]] = {}
        for row in repo.member_message_counts(iso(start), iso(end)):
            if row["author"] in ("user", "system"):
                continue
            s = speakers.setdefault(
                row["author"],
                {"member": row["author"], "name": names.get(row["author"], row["author"])}
                | {"life": 0, "replies": 0, "chars": 0},
            )
            s["life" if row["life"] else "replies"] += row["n"]
            s["chars"] += row["chars"]
        talk = sorted(speakers.values(), key=lambda s: -(s["life"] + s["replies"]))
        total_msgs = sum(s["life"] + s["replies"] for s in talk)
        for s in talk:
            s["share"] = round((s["life"] + s["replies"]) / total_msgs, 3) if total_msgs else 0.0
        fair = 1 / len(talk) if talk else 0
        loud = [s["name"] for s in talk if len(talk) > 1 and s["share"] > fair * 2]

        lines = repo.life_messages_between(iso(start), iso(end))[-500:]
        repeats: list[dict[str, Any]] = []
        for i, a in enumerate(lines):
            for b in lines[:i]:
                if similarity(a["text"], b["text"]) >= rules.stale_similarity:
                    repeats.append({"name": names.get(a["author"], a["author"]), "text": a["text"]})
                    break
        log = repo.guard_log_between(iso(start), iso(end))
        failed = sum(s["failed"] or 0 for s in steps)
        calls = sum(s["calls"] for s in steps)

        notes: list[str] = []
        if loud:
            notes.append(f"{'、'.join(loud)} 说得明显比别人多")
        if repeats:
            notes.append(f"有 {len(repeats)} 句和之前说过的话很像")
        if calls and failed / calls > 0.2:
            notes.append(f"调用出错比例偏高（{failed}/{calls}）")
        halts = [r for r in log if r["kind"] == HALT]
        if halts:
            notes.append(f"有 {len(halts)} 天生活被熔断停掉")
        daily = self.life_rules.daily_usd
        if daily > 0 and life_cost / days > daily:
            notes.append("生活平均每天花费超过了每日上限")
        return {
            "start": iso(start),
            "end": iso(end),
            "cost": {
                "total_usd": round(total_cost, 6),
                "life_usd": round(life_cost, 6),
                "life_per_day_usd": round(life_cost / days, 6),
                "life_daily_limit_usd": daily,
                "by_step": [
                    {**s, "failed": s["failed"] or 0, "cost_usd": round(s["cost_usd"], 6)}
                    for s in steps
                ],
            },
            "speakers": talk,
            "messages": {"total": total_msgs, "life": len(lines)},
            "repeats": {"count": len(repeats), "examples": repeats[: rules.report_examples]},
            "guard": {
                "stale": sum(1 for r in log if r["kind"] == STALE),
                "cap_days": sum(1 for r in log if r["kind"] == CAP),
                "halts": [{"at": r["at"], "reason": r["detail"]} for r in halts],
            },
            "errors": {"calls": calls, "failed": failed},
            "notes": notes or ["一切正常"],
        }

    def weekly(self, now: datetime, names: dict[str, str]) -> dict[str, Any] | None:
        """到了 report_weekday 且上一周还没有报告时写一份。返回新报告。"""
        if not self.rules.enabled:
            return None
        this_week, _ = week_window(now)
        if now.astimezone(this_week.tzinfo).isoweekday() < self.rules.report_weekday:
            return None
        start = this_week - timedelta(days=7)
        week = week_key(start)
        if any(r["week"] == week for r in self.rt.repo.life_reports(limit=1)):
            return None
        report = self.report(start, this_week, names)
        self.rt.repo.save_life_report(week, report)
        return {"week": week, **report}
