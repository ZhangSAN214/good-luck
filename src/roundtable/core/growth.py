"""P6 成长：每周一次，成员用自己的模型读上一周的经历，最多提几条自我档案的改动；程序把关、
另一位成员评审后才写进档案（docs/MEMBER_ENGINE.md「P6 成长」）。

- **唯一能以成长（growth）的身份写档案 personality / values / style / likes 的地方**（测试守卫）。
- 流程（每位成员每周一次，``growth.weekday`` 那天起反思上一周）：
  1. 程序整理一周的经历（``member/growth/week.py``）。有经历的天数少于 ``min_days`` 或条数少于
     ``min_lines`` 就跳过，记下原因，不调用模型。
  2. 成员用自己的模型（聊天档）提最多 ``max_changes`` 条改动（``growth_reflect``）。
  3. 程序把关（``member/growth/gate.py``）。提议不是 JSON、或提了但一条也没过时，带着被拦下的原因
     **最多重试一次**（借 dsh answer-reviewer；第二次的请求和第一次不同）。
  4. **评审**：从别的成员里按（周、成员）固定的随机数抽一位（不自评），用 TA 的模型给通过把关的
     每条打 1～5 分（``growth_review``），低于 ``min_score`` 的不改；评审没回应时这周都不改。
  5. 通过的每条各存成档案的一个新版本（能一条条撤回），全部结果记进成长时间线。
  6. 全部成员反思完，写一份本地备份（留 ``backups`` 份）。
- 花费：每次调用的预估相同（8 位相同），计入生活每天的上限（``chat.life.daily_usd``，
  步骤 ``growth``）、聊天月预算和总预算；额度不够时这周先不反思，下次推进再试。
- 只用于生活模式：``run(..., mode="work")`` 直接报错；工作模式的代码不 import 本模块。
"""

from __future__ import annotations

import hashlib
import random
from collections.abc import Callable
from datetime import date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

from roundtable.core.chat import USER, USER_LABEL, ChatMember, call_member
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.life import LifeEngine
from roundtable.core.member.card import TRAITS
from roundtable.core.member.feel import Feelings
from roundtable.core.member.growth import (
    Gate,
    GrowthLog,
    Verdict,
    Week,
    build,
    monday,
    parse,
    week_id,
)
from roundtable.core.member.growth import habits as hb
from roundtable.core.member.growth import report as rp
from roundtable.core.member.growth.gate import fresh_patch
from roundtable.core.member.growth.timeline import CHANGE, REJECTED, SKIP
from roundtable.core.member.mode import Mode, require_life
from roundtable.core.member.profile import ProfileStore
from roundtable.core.storage.repository import iso

ROOM = "growth"
TALENT_PATH = ("likes", "likes.擅长")  # 擅长（L10）写在档案的这一项
STEP = "growth"
AUTHOR = "growth"
NONE = "（没有）"
SELF_KEYS = (
    ("personality", "五项"),
    ("personality", "长处"),
    ("personality", "小毛病"),
    ("values", "最看重"),
    ("style", "口头禅"),
    ("likes", "最爱"),
    ("likes", "讨厌"),
)


def self_text(data: dict[str, Any]) -> str:
    """档案里成长能看、能改的几项（短项），给提示词用。"""
    lines = []
    for sec, key in SELF_KEYS:
        v = (data.get(sec) or {}).get(key)
        if v is None or v == [] or v == {}:
            continue
        if isinstance(v, dict):
            order = [k for k in TRAITS if k in v] + [k for k in v if k not in TRAITS]
            v = "、".join(f"{k} {v[k]}" for k in order)
        elif isinstance(v, list):
            v = "、".join(str(x) for x in v)
        lines.append(f"{sec}.{key}：{v}")
    return "\n".join(lines) or NONE


def seed_of(week: str, member: str) -> int:
    return int.from_bytes(hashlib.sha256(f"growth:{week}:{member}".encode()).digest()[:8], "big")


class GrowthKeeper:
    def __init__(
        self,
        rt: Any,
        members: Callable[[], list[ChatMember]],
        *,
        folder: Path | None = None,
    ) -> None:
        self.rt = rt
        self.members = members
        self.folder = folder  # 每周备份放在哪（为空时不写备份，如内存数据库）
        self.allow: Callable[[float], bool] = lambda estimate_usd: True  # 生活每天的上限
        self.log = GrowthLog(rt)
        self.gate = Gate(rt)
        self.profiles = ProfileStore(rt)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.engine.growth

    def _params(self) -> dict[str, Any]:
        params = dict(self.rt.config.roundtable.step_params.get("chat", {}))
        params["max_tokens"] = self.rules.max_tokens
        return params

    async def _call(self, member: ChatMember, role: str, variables: dict[str, str]) -> str | None:
        rt = self.rt
        if not self.allow(self.rules.estimate_usd):
            return None
        prompt = rt.prompts.render(role, rt.config.roundtable.prompts[role], **variables)
        completion = await call_member(
            rt,
            room=ROOM,
            model_id=member.model_id,
            prompt=prompt,
            step=STEP,
            params=self._params(),
            estimate_usd=self.rules.estimate_usd,
        )
        return None if completion is None else completion.text

    # --- 每周一次 ----------------------------------------------------------------------

    def due(self, today: date) -> date | None:
        """今天该反思哪一周（返回那一周的周一）；还没到每周的那一天时为 None。"""
        if today.isoweekday() < self.rules.weekday:
            return None
        return monday(today) - timedelta(days=7)

    async def tick(self, now: datetime) -> dict[str, int]:
        """推进到 now（带时区）：先累加习惯计数、补上个月的报告（都不花钱），
        再让上一周还没反思过的成员各反思一次。返回各结果的数量。"""
        out = {"members": 0, "changes": 0, "rejected": 0, "skipped": 0, "reports": 0}
        if not self.rules.enabled:
            return out
        if talents := self.talents(now):
            out["talents"] = talents
        self.count(now)
        out["reports"] = self.reports(now.date())
        start = self.due(now.date())
        if start is None:
            return out
        wid = week_id(start)
        everyone = self.members()
        ran = False
        for m in everyone:
            if self.log.done_week(m.id) == wid:
                continue
            got = await self.run(m, start, now.tzinfo, today=now.date())
            if got is None:
                continue  # 额度不够：下次推进再试
            ran = True
            out["members"] += 1
            for k in ("changes", "rejected", "skipped"):
                out[k] += got[k]
        if ran:
            self.log.backup(self.folder, wid, [m.id for m in everyone])
        return out

    # --- 擅长（L10）：生活引擎发现的擅长由成长写进档案（不花钱） --------------------------

    def talents(self, now: datetime) -> int:
        """把生活里觉醒 / 生疏了的擅长写进档案 ``likes.擅长``（以成长的身份，一项一个版本），
        并记进成长时间线（能一条条撤回）。证据只来自人物卡种子和生活练习，和工作无关。
        返回写了几条。用户锁住的 likes.擅长 不动。"""
        from roundtable.core.member.growth.gate import ADD, REMOVE
        from roundtable.core.member.profile import ProfileError
        from roundtable.core.member.story import Talents

        tal, n = Talents(self.rt), 0
        week = week_id(now.date())
        for m in self.members():
            for item in tal.take(m.id):
                domain = str(item.get("domain") or "")
                cur = self.profiles.current(m.id).get(TALENT_PATH[1])
                old = [str(x) for x in cur] if isinstance(cur, list) else []
                add = item.get("op") == "add"
                if not domain or (domain in old) == add:
                    continue
                new = [*old, domain] if add else [x for x in old if x != domain]
                why = str(item.get("why") or "")
                try:
                    p = self.profiles.change(
                        m.id,
                        {"likes": {"擅长": new or None}},
                        author=AUTHOR,
                        reason=f"生活里{'发现自己擅长' if add else '生疏了'}：{domain}",
                    )
                except ProfileError:
                    continue
                entry = {
                    "week": week,
                    "kind": CHANGE,
                    "op": ADD if add else REMOVE,
                    "path": TALENT_PATH[1],
                    "value": domain,
                    "delta": 0,
                    "old": old,
                    "new": new,
                    "why": why or ("好久没碰了" if not add else ""),
                    "quote": "",
                    "evidence": [why] if why else [],
                    "user": False,
                    "score": None,
                    "reason": "生活里发现的擅长（L10）" if add else "生活里生疏了（L10）",
                    "clash": "",
                    "version": p.version,
                    "reverted": False,
                }
                self.log.add(m.id, [entry])
                n += 1
        return n

    # --- 习惯、熟练度、月度报告（程序算，不花钱） ----------------------------------------

    def count(self, now: datetime) -> int:
        """把事件总线上新的事件累加进每位成员的习惯计数（第一次从头算全部经历）。"""
        until = iso(now)
        members = [m.id for m in self.members()]
        cursors = [self.log.load(m).get("cursor") or "" for m in members]
        if not members or min(cursors) >= until:
            return 0
        events = self.rt.repo.member_events_between(min(cursors), until)
        return sum(
            self.log.count(m, events, until, now.tzinfo, LifeEngine.place_name) for m in members
        )

    def habits(self, member: str) -> list[dict[str, Any]]:
        return hb.habits(self.log.load(member), self.rules.habits)

    def skills(self, member: str) -> list[dict[str, Any]]:
        domains = self.rt.config.roundtable.chat.memory.domains
        return hb.skills(self.rt.repo.memory_item_stats(member), domains)

    def report(self, member: str, month: str) -> dict[str, Any]:
        """这位成员 month（YYYY-MM）的「这个月的我」（不存）。"""
        repo = self.rt.repo
        days = {
            d["period"]
            for d in repo.memories(member, layer="day", limit=100)
            if str(d["period"]).startswith(month)
        }
        items = repo.memory_items(member, since_day=f"{month}-01", current=False, limit=5000)
        return rp.build(
            self.log.load(member),
            month,
            rules=self.rules,
            skills=self.skills(member),
            diary_days=len(days),
            items=sum(1 for it in items if it["day"].startswith(month)),
        )

    def reports(self, today: date) -> int:
        """上个月还没有报告的成员各写一份（那个月什么都没发生的不写）。返回写了几份。"""
        if not self.rules.report.enabled:
            return 0
        month = rp.prev_month(today)
        n = 0
        for m in self.members():
            if month in self.log.load(m.id)["reports"]:
                continue
            r = self.report(m.id, month)
            if r["diary_days"] or r["items"] or r["changes"]:
                self.log.save_report(m.id, r, self.skills(m.id))
                n += 1
        return n

    def week(self, member: str, start: date, tz: tzinfo | None) -> Week:
        return build(self.rt, member, start, tz, user_label=USER_LABEL, user_author=USER)

    async def run(
        self,
        member: ChatMember,
        start: date,
        tz: tzinfo | None,
        *,
        today: date | None = None,
        mode: Mode | str = Mode.LIFE,
    ) -> dict[str, int] | None:
        """一位成员反思 start 起的那一周。额度不够时返回 None（这周还算没反思）。"""
        require_life(mode)
        r = self.rules
        today = today or start + timedelta(days=7)
        week = self.week(member.id, start, tz)
        out = {"changes": 0, "rejected": 0, "skipped": 0}
        if week.days < r.min_days or len(week.lines) < r.min_lines:
            why = f"上周只有 {week.days} 天、{len(week.lines)} 条经历，太少了，这周不反思"
            self.log.add(
                member.id, [{"week": week.week, "kind": SKIP, "reason": why}], week=week.week
            )
            out["skipped"] = 1
            return out
        profile = self.profiles.current(member.id)
        variables = {
            "name": member.name,
            "week": f"{week.week}（{start.isoformat()} 起的 7 天）",
            "profile": self_text(profile.data),
            "feelings": self._feelings(member.id),
            "locked": "、".join(profile.locked) or NONE,
            "habits": self._habits(member.id),
            "lines": week.text(),
            "feedback": "没有",
            "max_changes": str(r.max_changes),
            "lists": "；".join(f"{s}.{k}" for s, keys in r.lists.items() for k in keys),
            "item_chars": str(r.item_chars),
            "emotions": "、".join(self.rt.config.roundtable.chat.engine.feel.names),
            "feel_chars": str(self.rt.config.roundtable.chat.engine.feel.grow_chars),
        }
        others = {
            m.id: self.profiles.current(m.id).data for m in self.members() if m.id != member.id
        }
        verdicts: list[Verdict] = []
        skip = ""
        for attempt in range(r.review.max_retries + 1):
            text = await self._call(member, "growth_reflect", variables)
            if text is None:
                return None
            proposals, skip = parse(text, r.max_changes)
            ledger = self.log.ledger(member.id, today)
            verdicts = (
                self.gate.check(member.id, proposals, week, ledger, others) if proposals else []
            )
            if proposals == [] or any(v.ok for v in verdicts):
                break  # 没想改，或者有能改的：不重试
            if attempt < r.review.max_retries:
                variables["feedback"] = self._feedback(proposals is None, verdicts)
        passed = [v for v in verdicts if v.ok]
        if passed and not await self._review(member, week, passed, profile.data):
            return None
        entries = []
        for v in verdicts:
            patch = fresh_patch(self.profiles.current(member.id).data, v.proposal) if v.ok else {}
            if patch:
                p = self.profiles.change(
                    member.id, patch, author=AUTHOR, reason=f"成长 {week.week}：{v.proposal.why}"
                )
                entries.append(self.log.entry(week.week, v, kind=CHANGE, version=p.version))
                out["changes"] += 1
            else:
                if v.ok:  # 评审期间档案被改过，这条已经改不了
                    v.ok, v.reason = False, "档案刚被改过，这条改不了了"
                entries.append(self.log.entry(week.week, v, kind=REJECTED))
                out["rejected"] += 1
        if not out["changes"]:
            reason = skip or ("提议都没通过把关" if verdicts else "这周没有想改的地方")
            entries.append({"week": week.week, "kind": SKIP, "reason": reason})
            out["skipped"] = 1
        self.log.add(member.id, entries, week=week.week)
        return out

    @staticmethod
    def _feedback(not_json: bool, verdicts: list[Verdict]) -> str:
        if not_json:
            return "上一次没有按格式只输出一个 JSON 对象。"
        return "\n".join(f"- {v.proposal.label()}：{v.reason}" for v in verdicts) or "没有"

    def _feelings(self, member: str) -> str:
        """给反思用：她现在对每种情绪的理解（人物卡起点或成长改写过的），还没有的写「还说不清」。"""
        feel = Feelings(self.rt)
        mine = feel.understanding(member)
        names = self.rt.config.roundtable.chat.engine.feel.names
        return "\n".join(f"{emo}：{mine.get(emo) or feel.rules.unknown}" for emo in names)

    def _habits(self, member: str) -> str:
        """给反思用：P2 的常做的事 + 全部经历累积的习惯和熟练度。"""
        row = self.rt.repo.memory_digest(member)
        parts = [(row or {}).get("text") or "", hb.text(self.habits(member), self.skills(member))]
        return "\n".join(p for p in parts if p) or NONE

    async def _review(
        self, member: ChatMember, week: Week, passed: list[Verdict], data: dict[str, Any]
    ) -> bool:
        """评审：抽一位别的成员给通过把关的每条打分（不自评）。评审没回应时返回 False。

        打分低于 min_score 的那条改成拦下。没有别的成员时不评审。"""
        r = self.rules.review
        others = [m for m in self.members() if m.id != member.id]
        if not r.enabled or not others:
            return True
        reviewer = random.Random(seed_of(week.week, member.id)).choice(
            sorted(others, key=lambda m: m.id)
        )
        cited = sorted({n for v in passed for n in v.proposal.evidence})
        lines = [week.line(n) for n in cited]
        evidence = "\n".join(f"{ln.n}. {ln.text}" for ln in lines if ln is not None)
        changes = "\n".join(
            f"{i}. {v.proposal.label()}；为什么：{v.proposal.why}；引用：{v.proposal.evidence}"
            f" 「{v.proposal.quote}」"
            for i, v in enumerate(passed, 1)
        )
        text = await self._call(
            reviewer,
            "growth_review",
            {
                "reviewer": reviewer.name,
                "name": member.name,
                "profile": self_text(data),
                "evidence": evidence or NONE,
                "changes": changes,
            },
        )
        if text is None:
            return False
        try:
            scores = extract_json_object(text).get("scores") or []
        except JSONOutputError:
            scores = []
        got: dict[int, int] = {}
        for s in scores if isinstance(scores, list) else []:
            if isinstance(s, dict):
                try:
                    got[int(s.get("id"))] = int(s.get("score"))
                except (TypeError, ValueError):
                    continue
        for i, v in enumerate(passed, 1):
            v.score = got.get(i)
            if v.score is None or v.score < r.min_score:
                v.ok, v.patch = False, {}
                v.reason = "评审没给分" if v.score is None else f"评审只给了 {v.score} 分，证据不够"
        return True
