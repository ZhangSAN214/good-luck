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
from roundtable.core.chat_actions import (
    card_from_item,
    card_text,
    clean_card,
    clean_stickers,
    sticker_list,
)
from roundtable.core.member.brain import Brain
from roundtable.core.member.voice import Said, before_voice, recent_words
from roundtable.core.office.apartment import schedule
from roundtable.core.persona import chat_persona
from roundtable.core.plain_talk import before_speaking, plain_prompt
from roundtable.core.storage.repository import iso

MOMENT_ROOM = "moments"
NOTHING = "没有特别的事"  # 生活场景里「今天的事」为空时的写法（life.scene）
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


def post_with_media(moment: dict[str, Any], names: dict[str, str]) -> str:
    """动态正文；感官整理过图片 / 视频 / 语音时，后面附上感知卡（成员「看到」的内容）。"""
    cards = (moment.get("meta") or {}).get("perception")
    if not cards:
        return moment["text"]
    from roundtable.core.perception import media_block  # perception 依赖 chat

    who = "用户" if moment["author"] == "user" else names.get(moment["author"], moment["author"])
    return f"{moment['text']}\n{media_block(who, cards)}"


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
            and self.worth_posting(e)
        ]
        if not events:
            return None
        return max(events, key=lambda e: (e["written"], _RANK.get(e["kind"], 0), e["at"]))

    def worth_posting(self, event: dict[str, Any]) -> bool:
        """有事可说：碰到了人、聊过天、或者今天办了活。一个人待着、什么也没发生的日子不发
        （``chat.engine.voice.check.moments_need_event``，成员系统 P5）。"""
        engine = self.rt.config.roundtable.chat.engine
        if not (engine.enabled and engine.voice.enabled and engine.voice.check.moments_need_event):
            return True
        return bool(event["written"]) or event["kind"] != "solo" or NOTHING not in event["topic"]

    def too_similar(self, text: str, recent: list[dict[str, Any]]) -> bool:
        return any(similarity(text, p["text"]) >= self.rules.similarity_max for p in recent)

    def recent_text(self, member: ChatMember) -> str:
        rows = self.rt.repo.moments(author=member.id, limit=3)
        return "\n".join(f"- {r['text']}" for r in rows) or NONE

    @staticmethod
    def post_text(moment: dict[str, Any]) -> str:
        """给成员看的动态正文：转发的卡片写成「[链接] 标题」。"""
        card = (moment.get("meta") or {}).get("card")
        text = moment["text"]
        return (
            f"{text}\n{card_text(card)}".strip() if card and card_text(card) not in text else text
        )

    def comments_text(self, moment: dict[str, Any]) -> str:
        names = self.names()
        lines = [f"{names.get(c['author'], c['author'])}：{c['text']}" for c in moment["comments"]]
        return "\n".join(lines) or NONE

    # --- 写 ----------------------------------------------------------------------

    async def _write(self, member: ChatMember, role: str, **variables: str) -> str | None:
        rt, rules = self.rt, self.rules
        if not self.allow(rules.estimate_usd):
            return None
        version = rt.config.roundtable.prompts[role]
        wants = rt.prompts.get(role, version).variables
        acts = rt.config.roundtable.chat.actions
        if "stickers" in wants:  # v3 起：能用表情包
            variables["stickers"] = (
                sticker_list(acts.stickers) if acts.enabled else "（不用表情包）"
            )
        variables = {k: v for k, v in variables.items() if k in wants}
        prompt = rt.prompts.render(
            role,
            version,
            name=member.name,
            persona=chat_persona(rt, member.id),
            memory=member_memory(rt, member.id, "\n".join(variables.values())),
            **variables,
        )
        prompt = plain_prompt(rt.prompts, rt.config, prompt)
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
        text = clean_reply(completion.text, member.name, rules.max_chars)
        text = clean_stickers(text, acts.stickers if acts.enabled else {}).strip()
        if not text:
            return None
        return await before_speaking(
            rt, text, room=MOMENT_ROOM, name=member.name, model_id=member.model_id
        )

    async def post(
        self,
        member: ChatMember,
        event: dict[str, Any],
        recent: list[dict[str, Any]],
        *,
        offline: bool = False,
    ) -> dict[str, Any] | None:
        """发一条；调用失败为 None，写出来和近期动态太像时不发、返回 {}。

        今天上网分享过东西、且策略同意时（默认一半），发成「一句话 + 转发卡片」。"""
        card = self.forward_card(member, event)
        forward = NONE
        if card:
            summary = card["summary"] or "（没有）"
            forward = f"{card_text(card)}\n来源：{card['source']}\n摘要：{summary}"
        scene = event["topic"]
        musing = Brain(self.rt, names=self.names()).musing(member.id, self.rt.budget.now())
        if musing:  # 朋友圈读大脑：和聊天里是同一个人、同一些心事
            scene = f"{scene}\n你这阵子心里挂着：{musing}"
        text = await self._write(
            member,
            "moment_post",
            scene=scene,
            recent=self.recent_text(member),
            forward=forward,
        )
        if not text:
            return None
        if self.too_similar(text, recent) or not await self._self_check(
            member, text, "moment", own=self._own_posts(member), others=self._others(member, recent)
        ):
            self.skip(member, event["id"])
            return {}
        meta: dict[str, Any] = {"life": True, "event": event["id"]}
        if (
            card
            and "forward"
            in self.rt.prompts.get(
                "moment_post", self.rt.config.roundtable.prompts["moment_post"]
            ).variables
        ):
            meta["card"] = card
        return self.rt.repo.add_moment(member.id, text, meta=meta, offline=offline)

    def forward_card(self, member: ChatMember, event: dict[str, Any]) -> dict[str, Any] | None:
        rt = self.rt
        since = iso(utc_day_window(rt.budget.now())[0])
        shared = rt.repo.web_items_shared_by(member.id, since)
        if not shared or not self.hub.policy.moment_forward(member.id, str(event["id"])):
            return None
        from roundtable.core.browse import feeds_of  # browse 依赖本模块

        kinds = {f.name: f.kind for f in feeds_of(rt)[0]}
        item = shared[0]
        acts = rt.config.roundtable.chat.actions
        return card_from_item(item, kinds.get(item["feed"], "blog"), acts.video_hosts)

    def publish(
        self,
        member: str,
        text: str = "",
        *,
        media: list[dict[str, Any]] | None = None,
        card: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """接入口：以成员身份发一条朋友圈（生成的配图 / 动图、转发卡片）。不调用模型、不花钱；
        什么时候发由成员系统决定。media 用 ``chat_actions.generated_item`` 生成。"""
        if member not in {m.id for m in self.hub.everyone()}:
            raise ValueError("没有这位成员")
        meta: dict[str, Any] = {"life": True}
        words = text.strip()
        if card is not None:
            checked = clean_card(card)
            if checked is None:
                raise ValueError("卡片的网址不对")
            meta["card"] = checked
        if media:
            meta["media"] = list(media)
            meta["caption" if words else "auto_text"] = words or True
        shown = words or ("[图片]" if media else card_text(meta["card"]) if card else "")
        if not shown:
            raise ValueError("没有要发的内容")
        if media and words:
            shown = f"{words}\n[图片]"
        return self.rt.repo.add_moment(member, shown, meta=meta)

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
            post=post_with_media({**moment, "text": self.post_text(moment)}, names),
            comments=self.comments_text(moment),
            task=task,
        )
        if not text:
            return None
        text = await self._self_check(
            member,
            text,
            "comment",
            own=self._own_comments(member),
            others=self._recent_comments(member, moment),
            named=reply_to is not None or moment["author"] == USER,
        )
        if not text:
            return {}  # 自查：和别人的评论差不多（「挺治愈」）——不评了
        return self.rt.repo.add_moment_comment(
            moment["id"], member.id, text, reply_to=reply_to, offline=offline
        )

    # --- 说出口前自查（成员系统 P5，core/member/voice）--------------------------------

    def _own_posts(self, member: ChatMember) -> tuple[str, ...]:
        return tuple(r["text"] for r in reversed(self.rt.repo.moments(author=member.id, limit=7)))

    def _others(self, member: ChatMember, recent: list[dict[str, Any]]) -> tuple:
        names = self.names()
        return tuple(
            (names.get(p["author"], p["author"]), p["text"])
            for p in recent
            if p["author"] not in (member.id, USER)
        )

    def _comment_rows(self) -> list[dict[str, Any]]:
        rows = [c for m in self.rt.repo.moments(limit=20) for c in m["comments"]]
        return sorted(rows, key=lambda c: c["id"])[-40:]

    def _own_comments(self, member: ChatMember) -> tuple[str, ...]:
        return tuple(c["text"] for c in self._comment_rows() if c["author"] == member.id)[-10:]

    def _recent_comments(self, member: ChatMember, moment: dict[str, Any]) -> tuple:
        names = self.names()
        rows = {c["id"]: c for c in [*self._comment_rows(), *moment["comments"]]}
        return tuple(
            (names.get(c["author"], c["author"]), c["text"])
            for c in sorted(rows.values(), key=lambda c: c["id"])
            if c["author"] not in (member.id, USER)
        )[-20:]

    async def _self_check(
        self,
        member: ChatMember,
        text: str,
        kind: str,
        *,
        own: tuple[str, ...],
        others: tuple,
        named: bool = False,
    ) -> str:
        """返回要发的话；空串表示不发（和自己最近的动态 / 别人的评论太像、套同一个开头……）。"""
        rt = self.rt
        said = Said(
            own=own,
            others=others,
            long=recent_words(rt, member.id, rt.budget.now()),
            repeat_similarity=(
                rt.config.roundtable.chat.engine.voice.check.post_repeat_similarity
                if kind == "moment"
                else None
            ),
        )
        return await before_voice(
            rt,
            text,
            said,
            member=member.id,
            room=MOMENT_ROOM,
            room_kind=kind,
            named=named,
            name=member.name,
            model_id=member.model_id,
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
            done = await self.comment(member, fresh, offline=offline)
            if done is None:
                break
            out["comments"] += bool(done)
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
            done = None if fresh is None else await self.comment(member, fresh)
            if done is None:
                break
            n += bool(done)
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
