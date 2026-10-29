"""成员的分层记忆（只存本地 SQLite，用户可以查看、修改、删除、一键清空）。

- 层：``note`` 随手记（用户写的或以后的事件）→ ``day`` 每天一段 → ``week`` 每周 → ``month`` 每月，
  再加一份 ``profile`` 长期档案（性格以外的近况、和谁关系好、用户提过的偏好）。
- 整理：``MemoryKeeper.catch_up()`` 把还没整理的日子按天压缩（最多补
  ``chat.memory.catch_up_days`` 天），过完的周、月再往上压一层；当天没有聊天的成员
  不调用模型。调用记在「记忆」隐藏聊天会话上，计入聊天月预算。
- 用到哪：聊天提示词里放一小段（``memory_context``，有字数上限）。**工作模式与办公室不读取**。
- 召回方式 ``chat.memory.recall``：``half_life``（默认，P2 记忆与联想）从记忆件里按和当前对话的
  相关度 × 鲜活度（带保底）× 重要度挑最多 ``inject_max`` 件，加上脑子里挂着的事和联想到的一件
  （``core/member/memory/``，不调用模型）；``layers``（旧）按时间固定带几层。
- 夜间整理（``night: true``，默认）：每位成员每天只调用一次（``memory_night``），写第一人称日记、
  拆记忆件、更新挂着的事，心情不平静时改几件旧记忆的语气和重要度（原文不改，留旧版）；
  不再另外滚动周 / 月和长期档案。``night: false`` 时用旧的 ``memory_items`` / ``memory_day``。
- 旧记忆第一次用半衰期时导入成记忆件（原文不改），之后的召回都看记忆件。
- 敏感内容（密码、证件号、银行卡、验证码、长串数字）整理时丢弃；提示词也要求不记。
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Any

from roundtable.core.chat import SYSTEM, USER, USER_LABEL, ChatMember, call_member
from roundtable.core.hometz import home_zone
from roundtable.core.member.memory import MemoryStore, Recaller
from roundtable.core.member.memory.night import apply, materials, nightly_links, parse
from roundtable.core.member.memory.store import WORK_WORKFLOWS, headless
from roundtable.core.recall import STAR
from roundtable.core.storage.repository import iso

MEMORY_ROOM = "memory"
LAYERS = ("note", "day", "week", "month", "profile")
LAST_DAY_KEY = "memory.last_day"
IMPORTED_KEY = "memory.items_imported"
CLEANED_KEY = "memory.items_cleaned_v1"
SENSITIVE = re.compile(r"密码|口令|身份证|护照号|银行卡|卡号|验证码|\d{11,}")
NONE = "（暂无）"


def clean_memory(text: str, max_chars: int) -> str:
    """去掉含敏感信息的行，截断到 max_chars。"""
    lines = [ln.strip() for ln in (text or "").strip().splitlines()]
    kept = [ln for ln in lines if ln and not SENSITIVE.search(ln)]
    out = "\n".join(kept)
    return out if len(out) <= max_chars else out[:max_chars].rstrip() + "…"


def week_id(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def month_id(day: date) -> str:
    return f"{day.year}-{day.month:02d}"


def day_window(day: date, tz: tzinfo | None) -> tuple[str, str]:
    """本地日期 day 的 [0 点, 次日 0 点)，换成库里的 UTC 时间戳。"""
    start = datetime.combine(day, time(0), tzinfo=tz) if tz else datetime.combine(day, time(0))
    if start.tzinfo is None:
        start = start.astimezone()  # 本机时区
    return iso(start), iso(start + timedelta(days=1))


def _cut(text: str, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "…"


def import_old_memories(rt: Any) -> int:
    """第一次用半衰期记忆时，把旧的分层记忆导入成记忆件（只做一次，原文不改）。返回导入件数。

    只导入长期档案、用户写的和标了 ★ 的行：日记、周 / 月小结本身还在（网页和翻日记都看得到），
    逐行再抄一份成记忆件只会和日记重复。只有标题的行（「10月24日回忆录：」）不导入。
    """
    repo = rt.repo
    if repo.setting(IMPORTED_KEY):
        return 0
    rows = []
    for m in repo.memories(limit=1_000_000):
        when = m["period"] if m["layer"] == "day" else m["created_at"][:10]
        if m["layer"] in ("week", "month"):
            when = m["created_at"][:10]
        lines = m["text"].splitlines() if m["layer"] in ("profile", "day") else [m["text"]]
        for i, raw in enumerate(ln.strip() for ln in lines):
            star = raw.startswith(STAR)
            text = raw.lstrip(STAR).strip()
            fact = star or m["layer"] == "profile" or m["source"] == "user"
            if not text or not fact or headless(text):
                continue
            if repo.memory_item_by_key(m["member"], f"note:{m['id']}"):
                continue  # 用户记的已经经服务写成记忆件了
            rows.append(
                {
                    "member": m["member"],
                    "text": text,
                    "day": when,
                    "kind": "fact" if fact else "episode",
                    "importance": 3 if star else (2 if fact else 1),
                    "source": "user" if m["source"] == "user" else "import",
                    "source_key": f"import:{m['id']}:{i}",
                }
            )
    n = repo.add_memory_items_bulk(rows)
    repo.set_setting(IMPORTED_KEY, iso(rt.budget.now()))
    return n


def clean_old_items(rt: Any) -> int:
    """清理一次已经存在的重复和空记忆件（只做一次，只改本机数据库）。返回删掉的件数。

    - 旧版导入时把日记、周 / 月小结逐行抄成的普通经历件（和日记重复）：只出现过一次、
      没被改过的删掉；日记原文不动。被合并过（命中多次）或改过语气、原文的保留。
    - 只有标题没有正文的件（用户自己写的除外）。
    """
    repo = rt.repo
    if repo.setting(CLEANED_KEY):
        return 0
    ids = {r["id"] for r in repo.memory_items_for_cleanup("import:", "episode")}
    ids |= {i for i, text in repo.memory_items_not_from("user") if headless(text)}
    for item_id in sorted(ids):
        repo.delete_memory_item(item_id)
    repo.set_setting(CLEANED_KEY, iso(rt.budget.now()))
    return len(ids)


def prepare_items(rt: Any) -> None:
    """用记忆件前：导入旧记忆、清理旧的重复和空件（各只做一次）。"""
    import_old_memories(rt)
    clean_old_items(rt)


def memory_context(
    rt: Any, member: str, max_chars: int, query: str = "", conversation: str | None = None
) -> str:
    """聊天提示词里的记忆。

    ``half_life``：按 query（当前对话、场景）召回，加挂着的事和联想，总长 ≤ max_chars；
    conversation（会话）相同且间隔不久的几次调用算一次对话，带过的不再带。
    ``layers``：长期档案、最近一月 / 一周 / 三天、最近几条随手记。
    """
    repo = rt.repo
    rules = rt.config.roundtable.chat.memory
    if rules.recall == "half_life":
        prepare_items(rt)
        now = rt.budget.now()
        got = Recaller(rt).recall(
            member,
            query,
            today=now.astimezone(home_zone(rt).tz()).date(),
            now=now,
            conversation=conversation,
            budget_chars=max_chars,
        )
        return got.text
    parts: list[str] = []

    def take(layer: str, n: int, label: str) -> None:
        for m in repo.memories(member, layer=layer, limit=n):
            when = f"（{m['period']}）" if m["period"] else ""
            parts.append(f"【{label}{when}】{m['text']}")

    take("profile", 1, "长期档案")
    take("month", 1, "上个月")
    take("week", 1, "最近一周")
    take("day", 3, "那天")
    take("note", 5, "记下的")
    text = "\n".join(parts)
    return _cut(text, max_chars) if text else NONE


class MemoryKeeper:
    def __init__(self, rt: Any, members: Any) -> None:
        """members：返回全部聊天成员（ChatMember 列表）的函数。"""
        self.rt = rt
        self.members = members
        # 额外的花费闸门（生活引擎接上每日上限）：返回 False 时不调用
        self.allow: Any = lambda estimate_usd: True
        # 现在的心情（-1～1，0 平静）；由状态插件接上（engine.state.mood_of），默认平静
        self.mood_of: Any = lambda member: 0.0
        # 关于用户的线索交给谁（成员系统 P8「关于你」：engine.about.observe）；默认不收
        self.on_clues: Any = lambda member, day, window, clues: None
        self.store = MemoryStore(rt)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.memory

    def _params(self) -> dict[str, Any]:
        params = dict(self.rt.config.roundtable.step_params.get("chat", {}))
        params["max_tokens"] = self.rules.max_tokens
        return params

    async def _call(self, member: ChatMember, role: str, **variables: str) -> str | None:
        rt = self.rt
        if not self.allow(self.rules.estimate_usd):
            return None
        prompt = rt.prompts.render(role, rt.config.roundtable.prompts[role], **variables)
        completion = await call_member(
            rt,
            room=MEMORY_ROOM,
            model_id=member.model_id,
            prompt=prompt,
            step="memory",
            params=self._params(),
            estimate_usd=self.rules.estimate_usd,
        )
        return None if completion is None else completion.text

    async def _write(self, member: ChatMember, role: str, **variables: str) -> str | None:
        text = await self._call(member, role, **variables)
        return None if text is None else clean_memory(text, self.rules.entry_chars) or None

    @property
    def nightly(self) -> bool:
        return self.rules.recall == "half_life" and self.rules.night

    def work_lines(self, member: ChatMember, day: date, tz: tzinfo | None) -> list[str]:
        """生活只读工作：这位成员自己在工作模式里参加过的场次记成经历（同一场只记一次）。

        往回看 ``work_lookback_days`` 天（匿名场次揭晓后才读，揭晓晚了也补上）。
        返回这一天新记下或发生在这一天的几件，给夜间整理当材料。不反写工作。"""
        vendor = {m.id: m.vendor for m in self.rt.config.models.models}
        start, _ = day_window(day - timedelta(days=self.rules.work_lookback_days - 1), tz)
        _, end = day_window(day, tz)
        lines = []
        for row in self.rt.repo.work_records_between(
            datetime.fromisoformat(start), datetime.fromisoformat(end), WORK_WORKFLOWS
        ):
            if vendor.get(row["model_id"]) != member.id:
                continue
            if row["anonymous"] and not row["revealed_at"]:
                continue  # 匿名场次揭晓前不读
            at = datetime.fromisoformat(row["at"]).astimezone(tz).date()
            w = self.store.add_work(member.id, row, at.isoformat())
            text = self.store.work_text(row)
            if (w.route != "skip" or at == day) and text not in lines:
                lines.append(text)
        return lines

    async def night(
        self, member: ChatMember, day: date, tz: tzinfo | None, *, offline: bool = False
    ) -> dict[str, Any] | None:
        """夜间整理（P2）：这一天只调用一次，写第一人称日记 + 记忆件 + 挂着的事。"""
        period = day.isoformat()
        repo = self.rt.repo
        if any(m["period"] == period for m in repo.memories(member.id, layer="day", limit=50)):
            return None
        log = self.day_log(member, day, tz)
        work = self.work_lines(member, day, tz)
        if not log and not work:
            return None  # 平静的一天不调用模型
        drift = nightly_links(self.rt, member.id, period)
        mats = materials(
            self.rt,
            member.id,
            name=member.name,
            day=period,
            log=log,
            work=work,
            mood=float(self.mood_of(member.id)),
            drift=drift,
        )
        text = await self._call(member, "memory_night", **mats.variables)
        if text is None:
            return None
        result = parse(text)
        counts = apply(
            self.rt, member.id, period, result, mats, clean=clean_memory, offline=offline
        )
        if result.about:  # 只交出去，画像由 P8 核对原话后写
            self.on_clues(member.id, period, day_window(day, tz), result.about)
        self.store.digest(member.id)
        if not counts["diary"]:
            return None
        return repo.memories(member.id, layer="day", limit=1)[0]

    def day_log(self, member: ChatMember, day: date, tz: tzinfo | None) -> str:
        """这一天成员在场的会话里的聊天（群、TA 的私聊）和没写成聊天的生活小事。

        每行「时:分 会话 名字：内容」或「时:分 （地点）做什么」，按时间排序。
        """
        start, end = day_window(day, tz)
        rooms = {r["id"]: r for r in self.rt.repo.chat_rooms()}
        names = {m.id: m.name for m in self.members()} | {USER: USER_LABEL}
        lines = []
        for msg in self.rt.repo.chat_messages_between(start, end):
            room = rooms.get(msg["room"])
            if room is None or msg["author"] == SYSTEM:
                continue
            if room["members"] and member.id not in room["members"]:
                continue
            at = datetime.fromisoformat(msg["created_at"]).astimezone(tz)
            title = "私聊" if room["kind"] == "dm" else room["title"]
            who = names.get(msg["author"], msg["author"])
            lines.append((at, f"{at:%H:%M} {title} {who}：{msg['text']}"))
        for ev in self.rt.repo.life_events_between(start, end):
            if ev["written"] or member.id not in ev["members"]:
                continue
            at = datetime.fromisoformat(ev["at"]).astimezone(tz)
            lines.append((at, f"{at:%H:%M} （{life_line(ev)}）"))
        lines.sort(key=lambda x: x[0])
        log = "\n".join(text for _, text in lines)
        limit = self.rules.log_chars
        return log if len(log) <= limit else log[-limit:]

    async def summarize_day(
        self, member: ChatMember, day: date, tz: tzinfo | None, *, offline: bool = False
    ) -> dict[str, Any] | None:
        period = day.isoformat()
        repo = self.rt.repo
        if any(m["period"] == period for m in repo.memories(member.id, layer="day", limit=50)):
            return None
        log = self.day_log(member, day, tz)
        if not log:
            return None
        if self.rules.recall == "half_life":
            quota = self.rules.daily_quota
            text = await self._write(
                member, "memory_items", name=member.name, day=period, log=log, quota=str(quota)
            )
            text = "\n".join((text or "").splitlines()[:quota])
            for line in text.splitlines():
                star = line.startswith(STAR)
                self.store.write(
                    member.id, line.lstrip(STAR), day=period, fact=star, importance=3 if star else 1
                )
        else:
            text = await self._write(member, "memory_day", name=member.name, day=period, log=log)
        if not text:
            return None
        return repo.add_memory(member.id, "day", text, period=period, offline=offline)

    async def roll(self, member: ChatMember, layer: str, period: str, span: str) -> Any:
        """把下一层（日 → 周、周 → 月）压成一段；已有或没有材料时不调用。"""
        repo = self.rt.repo
        if any(m["period"] == period for m in repo.memories(member.id, layer=layer, limit=50)):
            return None
        lower = "day" if layer == "week" else "week"
        belongs = week_id if layer == "week" else month_id
        items = [
            m
            for m in reversed(repo.memories(member.id, layer=lower, limit=60))
            if _period_in(m["period"], lower, belongs) == period
        ]
        if not items:
            return None
        text = await self._write(
            member,
            "memory_roll",
            name=member.name,
            span=span,
            items="\n".join(f"【{m['period']}】{m['text']}" for m in items),
        )
        if not text:
            return None
        added = repo.add_memory(member.id, layer, text, period=period)
        if layer == "month":
            await self.update_profile(member, text)
        return added

    async def update_profile(self, member: ChatMember, recent: str) -> Any:
        repo = self.rt.repo
        old = repo.memories(member.id, layer="profile", limit=1)
        if old and old[0]["source"] == "user":
            return None  # 用户自己写的档案不自动改
        text = await self._write(
            member,
            "memory_profile",
            name=member.name,
            profile=old[0]["text"] if old else NONE,
            recent=recent,
        )
        if not text:
            return None
        if old:
            repo.delete_memory(old[0]["id"])
        return repo.add_memory(member.id, "profile", text)

    async def catch_up(self, now: datetime, *, offline: bool = False) -> int:
        """整理到昨天为止还没整理的日子（最多 catch_up_days 天），再滚动过完的周和月。

        返回写入的记忆条数。now 带时区（本地时间决定「哪一天」）。
        """
        if not self.rules.enabled:
            return 0
        tz = now.tzinfo
        today = now.date()
        repo = self.rt.repo
        last = repo.setting(LAST_DAY_KEY)
        first = today - timedelta(days=self.rules.catch_up_days)
        if last:
            first = max(first, date.fromisoformat(last) + timedelta(days=1))
        written = 0
        members = self.members()
        day = first
        while day < today:
            for member in members:
                if self.nightly:
                    if await self.night(member, day, tz, offline=offline):
                        written += 1
                elif await self.summarize_day(member, day, tz, offline=offline):
                    written += 1
            repo.set_setting(LAST_DAY_KEY, day.isoformat())
            day += timedelta(days=1)
        if self.nightly:
            return written  # 一天一次调用：不再另外滚动周 / 月和长期档案
        this_week, this_month = week_id(today), month_id(today)
        for member in members:
            days = repo.memories(member.id, layer="day", limit=60)
            for w in sorted({_period_in(m["period"], "day", week_id) for m in days}):
                if w and w < this_week and await self.roll(member, "week", w, f"{w} 这一周"):
                    written += 1
            weeks = repo.memories(member.id, layer="week", limit=20)
            for mo in sorted({_period_in(m["period"], "week", month_id) for m in weeks}):
                if mo and mo < this_month and await self.roll(member, "month", mo, f"{mo} 这个月"):
                    written += 1
        return written


def life_line(event: dict[str, Any]) -> str:
    """生活小事在记忆材料里的一句：场景里的「地点：…」那一行。"""
    for line in (event.get("topic") or "").splitlines():
        if line.startswith("地点："):
            return line.removeprefix("地点：")
    return event.get("kind") or ""


def _period_in(period: str, layer: str, belongs: Any) -> str:
    """一条日 / 周记忆属于哪一周 / 哪个月。周按周四所在的月份算。"""
    try:
        if layer == "day":
            return belongs(date.fromisoformat(period))
        year, week = period.split("-W")
        return belongs(date.fromisocalendar(int(year), int(week), 4))
    except ValueError:
        return ""
