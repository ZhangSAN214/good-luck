"""朋友圈：成员根据当天真实发生的事发动态，互相点赞、评论；用户也能发、赞、评（docs/CHAT.md G5）。

- 发动态：生活引擎每次推进时，挑醒着、今天还没发够的成员，用 TA 今天的生活事件（场景由程序
  生成，见 ``core/life.py``）写一条；没有事件的成员不发。每人每天最多 ``posts_per_member`` 条。
- 点赞由代码决定（每位成员对每条动态按 ``like_chance`` 掷一次，种子由动态和成员派生），不花钱。
- 评论：每次推进最多 ``comments_per_tick`` 条，用户的动态优先；每位成员在一条动态下只主动评论一次，
  一条动态下成员评论最多 ``max_comments`` 条。
- 回应用户：用户发动态后马上有一两位成员评论；用户评论某位成员的动态（或回复成员的评论）时，
  那位成员回一句。这些都是对用户动作的回应，成员不会主动去找用户。
- 花费计入生活每日上限、聊天月预算和总预算；调用记在「朋友圈」隐藏聊天会话（step = moment）。
- 去重（不花钱）：别人 ``dedupe_hours`` 小时内已经发过的同一件事（同一条生活事件）不再发，改挑
  当天另一件事，没有就跳过；写出来的正文和近期任何人的动态太像（字符二元组 Jaccard ≥
  ``similarity_max``）也不发，并记下这位成员的这件事，之后不再为它花钱。
- 配图暂不做（默认关闭的选项留到以后）。
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.budget.guard import day_window as utc_day_window
from roundtable.core.chat import (
    USER,
    USER_LABEL,
    ChatHub,
    ChatMember,
    call_member,
    clean_reply,
    member_memory,
)
from roundtable.core.office.apartment import schedule
from roundtable.core.persona import chat_persona
from roundtable.core.storage.repository import iso

MOMENT_ROOM = "moments"
MOMENT_STEP = "moment"
SEEN_KEY = "moments.seen"
SKIPPED_KEY = "moments.skipped"  # 写出来太像而放弃的「成员:事件」，之后不再为它花钱
SKIPPED_KEEP = 200
NONE = "（暂无）"
_RANK = {"pair": 3, "gather": 2, "solo": 1}
_NOISE = re.compile(r"[\W_]+")


def shingles(text: str) -> set[str]:
    """去掉标点空白后的字符二元组（中文按字，不需要分词）。"""
    t = _NOISE.sub("", text.lower())
    return {t[i : i + 2] for i in range(len(t) - 1)} or ({t} if t else set())


def similarity(a: str, b: str) -> float:
    """两段话的字符二元组 Jaccard 相似度，0～1。"""
    x, y = shingles(a), shingles(b)
    if not x or not y:
        return 0.0
    return len(x & y) / len(x | y)


class Moments:
    def __init__(self, rt: Any, hub: ChatHub, allow: Callable[[float], bool]) -> None:
        self.rt = rt
        self.hub = hub
        self.allow = allow  # 生活每日上限

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.moments

    def names(self) -> dict[str, str]:
        return {m.id: m.name for m in self.hub.everyone()} | {USER: USER_LABEL}

    # --- 素材 --------------------------------------------------------------------

    def recent_posts(self, now: datetime) -> list[dict[str, Any]]:
        """去重要比对的近期动态（所有人的）。"""
        since = iso(now - timedelta(hours=self.rules.dedupe_hours))
        return self.rt.repo.moments(since=since, limit=100)

    def skipped(self) -> list[str]:
        raw = self.rt.repo.setting(SKIPPED_KEY)
        return json.loads(raw) if raw else []

    def skip(self, member: ChatMember, event_id: int) -> None:
        keep = [*self.skipped(), f"{member.id}:{event_id}"][-SKIPPED_KEEP:]
        self.rt.repo.set_setting(SKIPPED_KEY, json.dumps(keep))

    def event_for(
        self, member: ChatMember, now: datetime, recent: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """成员今天（本地日）最值得说、且还没人发过的一件事；没有时为 None。"""
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        recent = self.recent_posts(now) if recent is None else recent
        posted = {(p["meta"] or {}).get("event") for p in recent}
        skipped = set(self.skipped())
        events = [
            e
            for e in self.rt.repo.life_events_between(iso(day_start), iso(now + timedelta(hours=1)))
            if member.id in e["members"]
            and e["topic"]
            and e["id"] not in posted
            and f"{member.id}:{e['id']}" not in skipped
        ]
        if not events:
            return None
        return max(events, key=lambda e: (e["written"], _RANK.get(e["kind"], 0), e["at"]))

    def too_similar(self, text: str, recent: list[dict[str, Any]]) -> bool:
        return any(similarity(text, p["text"]) >= self.rules.similarity_max for p in recent)

    def recent_text(self, member: ChatMember) -> str:
        rows = self.rt.repo.moments(author=member.id, limit=3)
        return "\n".join(f"- {r['text']}" for r in rows) or NONE

    def comments_text(self, moment: dict[str, Any]) -> str:
        names = self.names()
        lines = [f"{names.get(c['author'], c['author'])}：{c['text']}" for c in moment["comments"]]
        return "\n".join(lines) or NONE

    # --- 写 ----------------------------------------------------------------------

    async def _write(self, member: ChatMember, role: str, **variables: str) -> str | None:
        rt, rules = self.rt, self.rules
        if not self.allow(rules.estimate_usd):
            return None
        prompt = rt.prompts.render(
            role,
            rt.config.roundtable.prompts[role],
            name=member.name,
            persona=chat_persona(rt, member.id),
            memory=member_memory(rt, member.id, "\n".join(variables.values())),
            **variables,
        )
        params = dict(rt.config.roundtable.step_params.get("chat", {}))
        params["max_tokens"] = rules.max_tokens
        completion = await call_member(
            rt,
            room=MOMENT_ROOM,
            model_id=member.model_id,
            prompt=prompt,
            step=MOMENT_STEP,
            params=params,
            estimate_usd=rules.estimate_usd,
        )
        if completion is None:
            return None
        return clean_reply(completion.text, member.name, rules.max_chars) or None

    async def post(
        self,
        member: ChatMember,
        event: dict[str, Any],
        recent: list[dict[str, Any]],
        *,
        offline: bool = False,
    ) -> dict[str, Any] | None:
        """发一条；调用失败为 None，写出来和近期动态太像时不发、返回 {}。"""
        text = await self._write(
            member, "moment_post", scene=event["topic"], recent=self.recent_text(member)
        )
        if not text:
            return None
        if self.too_similar(text, recent):
            self.skip(member, event["id"])
            return {}
        meta = {"life": True, "event": event["id"]}
        return self.rt.repo.add_moment(member.id, text, meta=meta, offline=offline)

    async def comment(
        self,
        member: ChatMember,
        moment: dict[str, Any],
        *,
        task: str = "评论这条动态。",
        reply_to: int | None = None,
        offline: bool = False,
    ) -> dict[str, Any] | None:
        names = self.names()
        text = await self._write(
            member,
            "moment_comment",
            author=names.get(moment["author"], moment["author"]),
            post=moment["text"],
            comments=self.comments_text(moment),
            task=task,
        )
        if not text:
            return None
        return self.rt.repo.add_moment_comment(
            moment["id"], member.id, text, reply_to=reply_to, offline=offline
        )

    # --- 推进 --------------------------------------------------------------------

    def like_round(self, moments: list[dict[str, Any]], members: list[ChatMember]) -> int:
        """点赞（不花钱）：每位成员对每条别人的动态掷一次，种子由动态和成员派生。"""
        liked = 0
        for moment in moments:
            for m in members:
                if m.id == moment["author"] or m.id in moment["likes"]:
                    continue
                if random.Random(f"like:{moment['id']}:{m.id}").random() < self.rules.like_chance:
                    liked += self.rt.repo.like_moment(moment["id"], m.id)
        return liked

    def awake(self, now: datetime) -> list[ChatMember]:
        return [
            m
            for m in self.hub.everyone()
            if m.available and schedule(m.id, now.hour, now.weekday())[1] != "sleep"
        ]

    async def tick(self, now: datetime, *, offline: bool = False) -> dict[str, int]:
        out = {"posts": 0, "comments": 0, "likes": 0, "skipped": 0}
        rules = self.rules
        if not rules.enabled:
            return out
        repo = self.rt.repo
        day_start = iso(utc_day_window(self.rt.budget.now())[0])
        rng = random.Random(f"moments:{now.replace(minute=0, second=0, microsecond=0)}")
        awake = self.awake(now)
        rng.shuffle(awake)
        for member in awake:
            if out["posts"] >= rules.posts_per_tick:
                break
            if repo.moments_by(member.id, day_start) >= rules.posts_per_member:
                continue
            recent = self.recent_posts(now)
            event = self.event_for(member, now, recent)
            if event is None:
                continue
            posted = await self.post(member, event, recent, offline=offline)
            if posted is None:
                return out  # 预算不够或调用失败：这次不再试
            if not posted:
                out["skipped"] += 1  # 和近期动态太像：不发，也不再换人重写（省钱）
                break
            out["posts"] += 1
        recent = repo.moments(since=iso(now - timedelta(hours=rules.react_hours)), limit=50)
        out["likes"] = self.like_round(recent, awake)
        pairs = []
        for moment in sorted(recent, key=lambda r: r["author"] != USER):  # 用户的动态优先
            said = {c["author"] for c in moment["comments"]}
            by_members = sum(c["author"] != USER for c in moment["comments"])
            room = rules.max_comments - by_members
            options = [m for m in awake if m.id != moment["author"] and m.id not in said]
            rng.shuffle(options)
            pairs += [(moment, m) for m in options[: max(room, 0)]]
        for moment, member in pairs[: rules.comments_per_tick]:
            fresh = repo.moment(moment["id"])
            if fresh is None:
                continue
            if await self.comment(member, fresh, offline=offline) is None:
                break
            out["comments"] += 1
        return out

    # --- 回应用户 ------------------------------------------------------------------

    async def react_to_user_post(self, moment_id: int) -> int:
        """用户发了动态：一两位在线成员评论（谁来由随机决定）。"""
        moment = self.rt.repo.moment(moment_id)
        if moment is None:
            return 0
        online = [m for m in self.hub.everyone() if m.available]
        rng = random.Random(f"user-moment:{moment_id}")
        rng.shuffle(online)
        n = 0
        for member in online[: min(2, self.rules.max_comments)]:
            fresh = self.rt.repo.moment(moment_id)
            if fresh is None or await self.comment(member, fresh) is None:
                break
            n += 1
        return n

    async def reply_to_user_comment(self, comment_id: int) -> dict[str, Any] | None:
        """用户评论了成员的动态，或回复了成员的评论：那位成员回一句。"""
        repo = self.rt.repo
        comment = repo.moment_comment(comment_id)
        if comment is None or comment["author"] != USER:
            return None
        moment = repo.moment(comment["moment_id"])
        if moment is None:
            return None
        target = moment["author"]
        if comment["reply_to"]:
            parent = repo.moment_comment(comment["reply_to"])
            if parent is not None:
                target = parent["author"]
        member = next((m for m in self.hub.everyone() if m.id == target and m.available), None)
        if member is None:
            return None
        task = f"用户在这里对你说：「{comment['text']}」。回复用户这条评论。"
        return await self.comment(member, moment, task=task, reply_to=comment_id)
