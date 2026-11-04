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
<<<<<<< HEAD
- 配图（``media.moment_photos``）：按概率画一张手机随手拍；拍法按权重抽，多数只拍东西 / 风景、
  她不入镜（不传形象参考图），画完在本机加一层手机味（``member_stickers.phone_look``）。
- 说话像人（M2，2026-11-02）：写成聊天的事件把那段聊天的原话节选喂给发帖（``<talk>``）；每条按
  权重抽一种样子（一句话、吐槽、碎碎念、按爱好、只发表情；评论「6」「笑死」「怎么弄的」）；
  动态 ``post_max_chars``、评论 ``comment_max_chars`` 字以内（在句子边界截）；发出前走口语收尾
  （``Lingo.finish``）；用大家互相起的昵称，同一条里一个人只留一种叫法（``one_name``）；
  模型觉得没什么可发 / 可评时输出 ``skip_word``，不发也不再为它花钱；待命、放空这种事不拿来发。
- 节奏像人（M3）：每人每天按 ``post_counts`` 抽 0～2 条、按 ``post_hours`` 抽本地时间（晚上多），
  到点后 ``post_window_hours`` 内有事才发；评论按 ``comment_counts`` 抽 0～4 条，谁来按 P4 亲近度
  加权，几分钟到几小时陆续到（计划存在设置 ``moments.plan``）；成员的评论楼主可能回一句、
  楼主不回时别人可能接一句，只回 1 轮。时间戳记在计划的时刻（不是推进的那一刻）。
=======
- 配图（``media.moment_photos``）：按概率画一张；拍法按权重抽，多数她本人入镜、抓拍不摆拍，
  拍东西时只拍值得炫的（她不入镜时不传形象参考图），画完在本机加一层手机味
  （``member_stickers.phone_look``）。
>>>>>>> origin/ccr-86975127-sv4cvy
"""

from __future__ import annotations

import json
import math
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
    generated_item,
    member_stickers,
    sticker_menu,
)
from roundtable.core.hometz import home_zone
from roundtable.core.member.brain import Brain
from roundtable.core.member.lingo import Lingo
from roundtable.core.member.voice import Said, before_voice, recent_words
from roundtable.core.moment_shots import event_time, pick_shot, pick_spec, place_tags
from roundtable.core.office.apartment import schedule
from roundtable.core.persona import chat_persona
from roundtable.core.plain_talk import before_speaking, plain_prompt
from roundtable.core.storage.repository import iso

MOMENT_ROOM = "moments"
NOTHING = "没有特别的事"  # 生活场景里「今天的事」为空时的写法（life.scene）
MOMENT_STEP = "moment"
PHOTO_BRIEF_STEP = "moment_photo_brief"  # 配图前她自己写的拍摄脚本
SEEN_KEY = "moments.seen"
PHOTO_JOB = "moment_photo"  # 游戏模式排队的朋友圈配图（docs/GAMEMODE.md）
DEFERRED = {"deferred": True}  # photo() 的返回：显卡让路，这张排队补画
SKIPPED_KEY = "moments.skipped"  # 写出来太像而放弃的「成员:事件」，之后不再为它花钱
SKIPPED_KEEP = 200
COMMENT_SKIPPED_KEY = "moments.comment_skipped"  # 自查没过的「动态:成员」：不再让这位重写
MISSES_KEY = (
    "moments.misses"  # 当天没发出去的次数：{"day", "posts": {成员: n}, "comments": {动态: n}}
)
PLAN_KEY = (
    "moments.plan"  # 评论计划：{动态 id: [{"m": 成员, "due": 时刻, "to": 回哪条评论, "done"}]}
)
NONE = "（暂无）"
PASS = "\x00pass"  # 模型说「不发」：不发，也不再为这件事 / 这条动态花钱
_SENTENCE_END = "。！？!?~～…\n"
_CLAUSE_END = "，,；;、 "
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


def trim(text: str, cap: int) -> str:
    """截到 cap 个字以内：优先在句子结尾断，其次在逗号断，都没有时硬截（不加省略号）。"""
    text = text.strip()
    if len(text) <= cap:
        return text
    head = text[: cap + 1]
    for marks in (_SENTENCE_END, _CLAUSE_END):
        cut = max(head.rfind(c) for c in marks)
        if cut >= cap // 3:
            return head[: cut + (1 if head[cut] in _SENTENCE_END else 0)].strip()
    return text[:cap].strip()


def whole_sentences(text: str, cap: int) -> str:
    """英文段落截到 cap 个字符以内，停在最后一个完整句子（拍摄脚本不停在半句）。"""
    text = text.strip()
    if len(text) <= cap:
        return text
    head = text[: cap + 1]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "), head.rfind("。"))
    return head[: cut + 1].strip() if cut >= cap // 3 else trim(text, cap)


def one_name(text: str, names: dict[str, str], nicks: dict[str, str]) -> str:
    """同一条里一个人只用一种叫法：起了昵称的人，把正式名字都换成昵称。"""
    for who, nick in nicks.items():
        name = names.get(who, "")
        if nick and name and name != nick and name in text and name not in nick:
            text = text.replace(name, nick)
    return text


def pick_form(forms: list[Any], seed: str) -> str:
    if not forms:
        return NONE
    rng = random.Random(seed)
    return rng.choices([f.text for f in forms], [f.weight for f in forms])[0]


def post_slots(rules: Any, member: str, day: datetime) -> list[datetime]:
    """这一天（本地日，day 是当天 0 点、带时区）这位成员打算几点发：0～N 个时刻，按先后。
    种子由成员和日期派生，同一天算几次都一样；所有成员同一套规则。"""
    rng = random.Random(f"moment-day:{member}:{day.date().isoformat()}")
    n = min(
        rng.choices(range(len(rules.post_counts)), rules.post_counts)[0], rules.posts_per_member
    )
    hours = sorted(rules.post_hours)
    picked = rng.choices(hours, [rules.post_hours[h] for h in hours], k=n) if n else []
    return sorted(day + timedelta(hours=h, minutes=rng.randrange(60)) for h in picked)


def spread_minutes(rng: random.Random, low: int, high: int) -> float:
    """几分钟到几小时：对数均匀（短的常见，长的也有）。"""
    return math.exp(rng.uniform(math.log(low), math.log(high)))


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

    def comment_skipped(self) -> set[str]:
        raw = self.rt.repo.setting(COMMENT_SKIPPED_KEY)
        return set(json.loads(raw)) if raw else set()

    def skip_comment(self, member: ChatMember, moment_id: int) -> None:
        raw = self.rt.repo.setting(COMMENT_SKIPPED_KEY)
        keep = [*(json.loads(raw) if raw else []), f"{moment_id}:{member.id}"][-SKIPPED_KEEP:]
        self.rt.repo.set_setting(COMMENT_SKIPPED_KEY, json.dumps(keep))
        self._miss("comments", str(moment_id))

    def misses(self, kind: str) -> dict[str, int]:
        """今天（UTC 日）没发出去的次数：posts 按成员、comments 按动态。"""
        raw = self.rt.repo.setting(MISSES_KEY)
        data = json.loads(raw) if raw else {}
        today = iso(utc_day_window(self.rt.budget.now())[0])
        return data.get(kind, {}) if data.get("day") == today else {}

    def _miss(self, kind: str, key: str) -> None:
        today = iso(utc_day_window(self.rt.budget.now())[0])
        data = {"day": today, "posts": self.misses("posts"), "comments": self.misses("comments")}
        data[kind][key] = data[kind].get(key, 0) + 1
        self.rt.repo.set_setting(MISSES_KEY, json.dumps(data))

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
        （``chat.engine.voice.check.moments_need_event``，成员系统 P5）；没写成聊天、也没办活的
        「待命」「放空」这类事也不发（M2：用户那边 65% 的动态是这种）。"""
        if not event.get("written") and NOTHING in event["topic"]:
            place = next((ln for ln in event["topic"].split("\n") if ln.startswith("地点")), "")
            if any(f"（{w}" in place for w in self.rules.dull_activities):
                return False
        engine = self.rt.config.roundtable.chat.engine
        if not (engine.enabled and engine.voice.enabled and engine.voice.check.moments_need_event):
            return True
        return (
            bool(event.get("written"))
            or event.get("kind") != "solo"
            or NOTHING not in event["topic"]
        )

    def talk_text(self, event: dict[str, Any]) -> str:
        """写成聊天的事件：那段聊天的原话节选（「名字：原话」，每句截短）；没写成聊天时为（暂无）。"""
        rules = self.rules
        if not (event.get("written") and event.get("room") and rules.talk_lines):
            return NONE
        start = datetime.fromisoformat(event["at"])
        rows = [
            r
            for r in self.rt.repo.chat_messages_between(
                event["at"], iso(start + timedelta(hours=rules.talk_hours))
            )
            if r["room"] == event["room"] and r["text"]
        ][: rules.talk_lines]
        names = self.names()
        return (
            "\n".join(
                f"{names.get(r['author'], r['author'])}：{trim(r['text'], rules.talk_line_chars)}"
                for r in rows
            )
            or NONE
        )

    def calls_text(self, member: ChatMember) -> str:
        """她平时对大家的叫法（大家互相起的昵称）；没起过时为（暂无）。"""
        names = self.names()
        nicks = self.nicks(member)
        return "\n".join(f"{names.get(k, k)} → {v}" for k, v in nicks.items() if k in names) or NONE

    def nicks(self, member: ChatMember) -> dict[str, str]:
        return {k: v for k, v in Lingo(self.rt, names=self.names()).nicks(member.id).items() if v}

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

    async def _write(self, member: ChatMember, role: str, cap: int, **variables: str) -> str | None:
        """写一条。None：没钱或调用失败；PASS：她说不发；空串：只甩卡片 / 照片不写字。"""
        rt, rules = self.rt, self.rules
        if not self.allow(rules.estimate_usd):
            return None
        version = rt.config.roundtable.prompts[role]
        wants = rt.prompts.get(role, version).variables
        acts = rt.config.roundtable.chat.actions
        if "stickers" in wants:  # v3 起：能用表情包
            variables["stickers"] = (
                sticker_menu(rt, member.id, member.name) if acts.enabled else "（不用表情包）"
            )
        if "calls" in wants:  # v4 起：大家互相起的昵称
            variables["calls"] = self.calls_text(member)
        variables.setdefault("skip", rules.skip_word)
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
        word = _NOISE.sub("", text)
        if word == _NOISE.sub("", rules.skip_word):
            return PASS
        if rules.bare_word and word == _NOISE.sub("", rules.bare_word):
            return ""
        text = trim(text, cap)
        text = clean_stickers(text, member_stickers(rt, member.id, member.name)).strip()
        if not text:
            return PASS
        return await before_speaking(
            rt, text, room=MOMENT_ROOM, name=member.name, model_id=member.model_id
        )

    def finish(self, member: ChatMember, text: str, seed: str) -> str:
        """口语收尾（和群聊同一套，只调用）+ 同一条里一个人只用一种叫法。"""
        names = self.names()
        out = Lingo(self.rt, names=names).finish(member.id, text, rng=random.Random(seed))[0]
        return one_name(out or text, names, self.nicks(member))

    async def post(
        self,
        member: ChatMember,
        event: dict[str, Any],
        recent: list[dict[str, Any]],
        *,
        offline: bool = False,
        at: datetime | None = None,
        slot: int | None = None,
    ) -> dict[str, Any] | None:
        """发一条；调用失败为 None，写出来和近期动态太像、或她说不发时不发、返回 {}。

        今天上网分享过东西、且策略同意时（默认一半），发成「一句话 + 转发卡片」（可以不写字）；
        会配照片时也可以只发图。at：记在哪个时刻（计划发的时间），slot：今天第几条。"""
        rules = self.rules
        card = self.forward_card(member, event)
        forward = NONE
        if card:
            summary = card["summary"] or "（没有）"
            forward = f"{card_text(card)}\n来源：{card['source']}\n摘要：{summary}"
        snap = card is None and self.photo_planned(member, event)
        scene = event["topic"]
        musing = Brain(self.rt, names=self.names()).musing(member.id, self.rt.budget.now())
        if musing:  # 朋友圈读大脑：和聊天里是同一个人、同一些心事
            scene = f"{scene}\n你这阵子心里挂着：{musing}"
        seed = f"{event['id']}:{member.id}"
        form = (
            rules.forward_form
            if card
            else rules.photo_form
            if snap
            else pick_form(rules.post_forms, f"post-form:{seed}")
        ) or NONE
        bare = f"，或「{rules.bare_word}」" if (card or snap) and rules.bare_word else ""
        text = await self._write(
            member,
            "moment_post",
            rules.post_max_chars,
            scene=scene,
            talk=self.talk_text(event),
            form=form,
            recent=self.recent_text(member),
            forward=forward,
            bare=bare,
        )
        if text is None:
            return None
        if text == PASS or (not text and not (card or snap)):
            self.skip(member, event["id"])  # 她觉得没什么可发：这件事不再花钱
            return {}
        if text and (
            self.too_similar(text, recent)
            or not await self._self_check(
                member,
                text,
                "moment",
                own=self._own_posts(member),
                others=self._others(member, recent),
            )
        ):
            self.skip(member, event["id"])
            self._miss("posts", member.id)
            return {}
        if text:
            text = self.finish(member, text, f"moment:{seed}")
        meta: dict[str, Any] = {"life": True, "event": event["id"]}
        if slot is not None:
            meta["slot"] = slot
        if (
            card
            and "forward"
            in self.rt.prompts.get(
                "moment_post", self.rt.config.roundtable.prompts["moment_post"]
            ).variables
        ):
            meta["card"] = card
        item = await self.photo(member, event, text or event["topic"]) if snap else None
        deferred = item is DEFERRED
        if deferred:  # 游戏模式：先发文字，图排队，显卡空了补上（只想发图的这次照旧不发）
            item = None
            if text:
                meta["photo_pending"] = True
        if item is not None:  # 配图：文字照旧给成员看，图片只多一个「[图片]」占位
            meta |= {"media": [item], "photo": True}
            meta["caption" if text else "auto_text"] = text or True
            text = f"{text}\n[图片]" if text else "[图片]"
        elif not text:
            if not card:  # 只想发图却没画出来：这次不发，这件事也不再花钱
                self.skip(member, event["id"])
                return {}
            text = card_text(card)
        posted = self.rt.repo.add_moment(
            member.id, text, meta=meta, offline=offline, created_at=iso(at) if at else None
        )
        gamemode = getattr(self.rt, "gamemode", None)
        if meta.get("photo_pending") and gamemode is not None and posted.get("id"):
            gamemode.defer(
                PHOTO_JOB,
                f"{member.name}的朋友圈配图",
                {
                    "moment": posted["id"],
                    "member": member.id,
                    "text": text,
                    "event": {k: event.get(k) for k in ("id", "topic", "place", "at", "members")},
                },
            )
        return posted

    def photo_planned(self, member: ChatMember, event: dict[str, Any]) -> bool:
        """这条会不会配图（和 ``photo`` 同一个种子；没开、没参考卡、超了张数时为 False）。"""
        rt = self.rt
        rules = rt.config.roundtable.media.moment_photos
        if not (rt.config.roundtable.media.enabled and rules.enabled) or rt.chat_media_dir is None:
            return False
        if member.id not in rt.config.personas.sticker_cards:
            return False
        place = str(event.get("place") or "")
        if place and not place_tags(place):
            return False  # 公寓楼外的事（出门、旅行）不配图：配图只画公寓里（11-02 用户）
        if random.Random(f"photo:{event['id']}:{member.id}").random() >= rules.chance:
            return False
        day_start = iso(utc_day_window(rt.budget.now())[0])
        today = [
            m
            for m in rt.repo.moments(since=day_start, limit=200)
            if m["meta"].get("photo") or m["meta"].get("photo_pending")
        ]
        mine = sum(m["author"] == member.id for m in today)
        return len(today) < rules.per_day and mine < rules.per_member_day

    async def photo(
        self, member: ChatMember, event: dict[str, Any], text: str, *, planned: bool = False
    ) -> dict[str, Any] | None:
        """按概率给这条动态画一张配图（不用审）；不配、超了每天的张数或画不出来时为 None；
        游戏模式下显卡让路、要排队时为 ``DEFERRED``。planned：已经定了要配（补画时）。"""
        from roundtable.core.chat_media import ChatMediaStore, MediaError
        from roundtable.core.member_stickers import (
            MemberStickers,
            PhotoDeferred,
            StickerBudgetExceeded,
            StickerError,
            StickerUnavailable,
        )

        rt = self.rt
        rules = rt.config.roundtable.media.moment_photos
        if not planned and not self.photo_planned(member, event):
            return None
        place = str(event.get("place") or "")
        rng = random.Random(f"photo:{event['id']}:{member.id}")
        rng.random()  # 和 photo_planned 掷的是同一次
        try:
            habit = rt.config.personas.sticker_cards[member.id].photo
            shot = pick_shot(rules, habit, rng)
            names = self.names()
            others = [(i, names.get(i, i)) for i in event.get("members") or [] if i != member.id]
            spec = pick_spec(
                rules,
                habit,
                shot,
                event_id=event["id"],
                member_id=member.id,
                time=event_time(event.get("at"), home_zone(rt).tz()),
                place=place,
            )
            brief = await self.photo_brief(member, event, text, shot.text, spec, others, place)
            data, mime = await MemberStickers(rt, rt.sticker_dir).photo(
                member.id,
                event["topic"],
                text,
                shot.text,
                refs=shot.her != "none",
                seed=f"{event['id']}:{member.id}",
                place=place,
                her=shot.her,
                shot_tags=shot.tags,
                spec=spec,
                others=others,
                brief=brief,
            )
            return generated_item(ChatMediaStore(rt.chat_media_dir), data, mime)
        except PhotoDeferred:
            return DEFERRED
        except (StickerBudgetExceeded, StickerError, StickerUnavailable, MediaError, ValueError):
            return None  # 没钱、没参考图、画不出来：照样发文字

    async def finish_photo(self, payload: dict[str, Any]) -> None:
        """补画排队的配图（游戏模式结束、显卡连续空闲后，由 GameMode 逐件调用）。
        动态删了、成员不在了就算了；显卡又忙了抛 StillBusy 放回队首；画不出来就去掉「待配图」。"""
        from roundtable.core.gamemode import StillBusy

        repo = self.rt.repo
        moment = repo.moment(int(payload["moment"]))
        member = next((m for m in self.hub.everyone() if m.id == payload.get("member")), None)
        if moment is None or member is None or not moment["meta"].get("photo_pending"):
            return
        text = str(payload.get("text") or "")
        item = await self.photo(member, dict(payload["event"]), text, planned=True)
        if item is DEFERRED:
            raise StillBusy
        meta = {k: v for k, v in moment["meta"].items() if k != "photo_pending"}
        if item is not None:
            meta |= {"media": [*meta.get("media", []), item], "photo": True, "caption": text}
        repo.set_moment_meta(moment["id"], meta)

    async def photo_brief(
        self,
        member: ChatMember,
        event: dict[str, Any],
        text: str,
        shot: str,
        spec: Any,
        others: list[tuple[str, str]],
        place: str,
    ) -> str:
        """画图前由她自己（用她的模型）把这条动态写成一段具体的拍摄脚本：谁在做什么、在画面哪一格、
        前景和引导线、关键道具的状态（11-02 用户看手写脚本的样张后要求）。关掉、没钱或失败时为空
        （照旧只用抽好的拍摄要求）。"""
        rt = self.rt
        rules = rt.config.roundtable.media.moment_photos
        role = "moment_photo_brief"
        version = rt.config.roundtable.prompts.get(role)
        if not rules.brief or version is None or not self.allow(rules.brief_estimate_usd):
            return ""
        from roundtable.core.member_stickers import MemberStickers

        stickers = MemberStickers(rt, rt.sticker_dir)
        people, _ = stickers.people(member.id, member.name, others, landlord=spec.landlord)
        room, _ = stickers.room(member.id, place)
        prompt = rt.prompts.render(
            role,
            version,
            name=member.name,
            scene=event["topic"],
            post=text,
            shot=shot,
            room=room,
            people=people,
            **spec.variables(),
        )
        params = dict(rt.config.roundtable.step_params.get("chat", {}))
        # 不思考：思考模型会把上限全花在思考上、正文为空（11-02 试画 DeepSeek 400 / 2000 都空）
        params["max_tokens"] = rules.brief_max_tokens
        params["reasoning"] = {"enabled": False}
        completion = await call_member(
            rt,
            room=MOMENT_ROOM,
            model_id=member.model_id,
            prompt=prompt,
            step=PHOTO_BRIEF_STEP,
            params=params,
            estimate_usd=rules.brief_estimate_usd,
        )
        if completion is None:
            return ""
        return whole_sentences(" ".join(completion.text.split()), rules.brief_max_chars)

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
        at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """写一条评论（reply_to：楼中楼）。None：没钱或调用失败；{}：不评（自查没过或她说不评）。"""
        names = self.names()
        rules = self.rules
        forms = rules.reply_forms if reply_to is not None else rules.comment_forms
        seed = f"{moment['id']}:{member.id}:{reply_to or ''}"
        text = await self._write(
            member,
            "moment_comment",
            rules.comment_max_chars,
            author=names.get(moment["author"], moment["author"]),
            post=post_with_media({**moment, "text": self.post_text(moment)}, names),
            comments=self.comments_text(moment),
            task=task,
            form=pick_form(forms, f"comment-form:{seed}"),
        )
        if text is None:
            return None
        if not text or text == PASS:  # 她没什么想说的：这条不评，也不算写废
            self._skip_quietly(member, moment["id"])
            return {}
        text = await self._self_check(
            member,
            text,
            "comment",
            own=self._own_comments(member),
            others=self._recent_comments(member, moment),
            named=reply_to is not None or moment["author"] == USER,
        )
        if not text:
            self.skip_comment(member, moment["id"])
            return {}  # 自查：和别人的评论差不多（「挺治愈」）——不评了，也不再让她重写
        text = self.finish(member, text, f"comment:{seed}")
        return self.rt.repo.add_moment_comment(
            moment["id"],
            member.id,
            text,
            reply_to=reply_to,
            offline=offline,
            created_at=iso(at) if at else None,
        )

    def _skip_quietly(self, member: ChatMember, moment_id: int) -> None:
        raw = self.rt.repo.setting(COMMENT_SKIPPED_KEY)
        keep = [*(json.loads(raw) if raw else []), f"{moment_id}:{member.id}"][-SKIPPED_KEEP:]
        self.rt.repo.set_setting(COMMENT_SKIPPED_KEY, json.dumps(keep))

    # --- 评论计划（M3：陆续到、看亲疏、楼中楼 1 轮）----------------------------------

    def plans(self) -> dict[str, list[dict[str, Any]]]:
        raw = self.rt.repo.setting(PLAN_KEY)
        return json.loads(raw) if raw else {}

    def save_plans(self, plans: dict[str, list[dict[str, Any]]]) -> None:
        self.rt.repo.set_setting(PLAN_KEY, json.dumps(plans, ensure_ascii=False))

    def closeness(self, member: str, other: str) -> float:
        from roundtable.core.member.relation import RelationPlugin  # relation 不依赖本模块

        try:
            return RelationPlugin(self.rt).closeness(member, other, self.rt.budget.now())
        except Exception:  # 关系插件关着或数据坏了：当生人
            return 0.0

    def plan_comments(self, moment: dict[str, Any]) -> list[dict[str, Any]]:
        """这条动态会有几条评论、谁来、各隔多久到（种子由动态派生，结果固定）。

        条数按 ``comment_counts`` 抽（0～4，用户的动态至少 1 条）；人按权重
        1 + ``closeness_bias`` × 和作者的亲近度不放回地抽；时间是发出后几分钟到几小时。"""
        rules = self.rules
        rng = random.Random(f"moment-plan:{moment['id']}")
        k = rng.choices(range(len(rules.comment_counts)), rules.comment_counts)[0]
        if moment["author"] == USER:
            k = max(k, 1)
        pool = sorted(
            (m for m in self.hub.everyone() if m.available and m.id != moment["author"]),
            key=lambda m: m.id,
        )
        weights = [1 + rules.closeness_bias * self.closeness(m.id, moment["author"]) for m in pool]
        chosen: list[str] = []
        while pool and len(chosen) < min(k, rules.max_comments):
            i = rng.choices(range(len(pool)), weights)[0]
            chosen.append(pool.pop(i).id)
            weights.pop(i)
        start = datetime.fromisoformat(moment["created_at"])
        low, high = rules.comment_delay_minutes
        delays = sorted(spread_minutes(rng, low, high) for _ in chosen)
        return [
            {"m": who, "due": iso(start + timedelta(minutes=d)), "to": None}
            for who, d in zip(chosen, delays, strict=True)
        ]

    def plan_reply(self, moment: dict[str, Any], comment: dict[str, Any]) -> dict[str, Any] | None:
        """成员的一条评论有没有人回（楼中楼，只回 1 轮）：楼主按 ``reply_author`` + 亲近度回；
        楼主不回时另一位按 ``reply_peer`` 接一句（越熟越可能是 TA）。"""
        rules = self.rules
        by, author = comment["author"], moment["author"]
        if by == USER or comment.get("reply_to"):
            return None
        rng = random.Random(f"moment-reply:{comment['id']}")
        live = {m.id for m in self.hub.everyone() if m.available}
        who = None
        if author != USER and author != by and author in live:
            chance = rules.reply_author + rules.reply_close * self.closeness(author, by)
            if rng.random() < chance:
                who = author
        if who is None and rng.random() < rules.reply_peer:
            pool = sorted(live - {by, author})
            if pool:
                weights = [1 + rules.closeness_bias * self.closeness(m, by) for m in pool]
                who = rng.choices(pool, weights)[0]
        if who is None:
            return None
        low, high = rules.reply_delay_minutes
        due = datetime.fromisoformat(comment["created_at"]) + timedelta(
            minutes=spread_minutes(rng, low, high)
        )
        return {"m": who, "due": iso(due), "to": comment["id"]}

    def reply_task(self, member: ChatMember, comment: dict[str, Any]) -> str:
        names = self.names()
        who = self.nicks(member).get(comment["author"]) or names.get(
            comment["author"], comment["author"]
        )
        return self.rules.reply_task.format(who=who, text=comment["text"])

    def asleep(self, member: str, at: datetime) -> bool:
        local = home_zone(self.rt).local(at)
        return schedule(member, local.hour, local.weekday())[1] == "sleep"

    async def run_plans(
        self, now: datetime, recent: list[dict[str, Any]], *, offline: bool = False
    ) -> int:
        """到点的评论写出来（按计划的时刻先后，每次推进最多 ``comments_per_tick`` 条）。"""
        rules, repo = self.rules, self.rt.repo
        plans = self.plans()
        live = {str(m["id"]) for m in recent}
        plans = {k: v for k, v in plans.items() if k in live}  # 过了 react_hours 的不再管
        for moment in recent:
            if str(moment["id"]) not in plans:  # 用户的动态一般已由 react_to_user_post 排好
                plans[str(moment["id"])] = self.plan_comments(moment)
        members = {m.id: m for m in self.hub.everyone()}
        due = sorted(
            ((e["due"], mid, e) for mid, es in plans.items() for e in es if not e.get("done")),
            key=lambda x: x[0],
        )
        written = 0
        for when, mid, entry in due:
            if when > iso(now) or written >= rules.comments_per_tick:
                break
            moment = repo.moment(int(mid))
            member = members.get(entry["m"])
            entry["done"] = True
            if moment is None or member is None or not member.available:
                continue
            at = datetime.fromisoformat(when)
            if self.asleep(member.id, at):
                continue  # 到点时她睡着了：这条就没有了
            if self.misses("comments").get(mid, 0) >= rules.comment_misses:
                continue  # 这条底下已经写废了几条：大家都没什么新话可说
            if f"{mid}:{member.id}" in self.comment_skipped():
                continue
            if entry["to"] is None:
                said = {c["author"] for c in moment["comments"] if not c["reply_to"]}
                tops = sum(c["author"] != USER and not c["reply_to"] for c in moment["comments"])
                if member.id in said or tops >= rules.max_comments:
                    continue
                done = await self.comment(member, moment, offline=offline, at=at)
            else:
                parent = repo.moment_comment(entry["to"])
                if parent is None:
                    continue
                task = self.reply_task(member, parent)
                done = await self.comment(
                    member, moment, task=task, reply_to=entry["to"], offline=offline, at=at
                )
            if done is None:
                entry.pop("done")  # 没钱或调用失败：下次再说
                break
            if done:
                written += 1
                follow = self.plan_reply(moment, done)
                if follow is not None:
                    plans[mid].append(follow)
        self.save_plans(plans)
        return written

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

    def due_slot(self, member: ChatMember, now: datetime) -> tuple[int, datetime] | None:
        """今天（本地日）到点、还没发过、也没过期的那一条：（第几条, 计划的时刻）。"""
        rules = self.rules
        local = home_zone(self.rt).local(now)
        day = local.replace(hour=0, minute=0, second=0, microsecond=0)
        mine = self.rt.repo.moments(author=member.id, since=iso(day), limit=20)
        if len(mine) >= rules.posts_per_member:
            return None
        used = {(m["meta"] or {}).get("slot") for m in mine}
        window = timedelta(hours=rules.post_window_hours)
        for i, at in enumerate(post_slots(rules, member.id, day)):
            if i not in used and at <= local < at + window:
                return i, at
        return None

    async def tick(self, now: datetime, *, offline: bool = False) -> dict[str, int]:
        out = {"posts": 0, "comments": 0, "likes": 0, "skipped": 0}
        rules = self.rules
        if not rules.enabled:
            return out
        repo = self.rt.repo
        rng = random.Random(f"moments:{now.replace(minute=0, second=0, microsecond=0)}")
        awake = self.awake(now)
        rng.shuffle(awake)
        for member in awake:
            if out["posts"] >= rules.posts_per_tick:
                break
            slot = self.due_slot(member, now)
            if slot is None:
                continue  # 今天不发 / 还没到点 / 已经发过
            if self.misses("posts").get(member.id, 0) >= rules.post_misses:
                continue  # 今天写了几次都没过自查：今天不发了（省钱）
            recent = self.recent_posts(now)
            event = self.event_for(member, now, recent)
            if event is None:
                continue  # 到点了但没事可说：不发
            at = max(slot[1], datetime.fromisoformat(event["at"]))
            posted = await self.post(
                member, event, recent, offline=offline, at=min(at, now), slot=slot[0]
            )
            if posted is None:
                return out  # 预算不够或调用失败：这次不再试
            if not posted:
                out["skipped"] += 1  # 和近期动态太像 / 她说不发：不发，也不再换人重写（省钱）
                continue
            out["posts"] += 1
        recent = repo.moments(since=iso(now - timedelta(hours=rules.react_hours)), limit=50)
        out["likes"] = self.like_round(recent, awake)
        out["comments"] = await self.run_plans(now, recent, offline=offline)
        return out

    # --- 回应用户 ------------------------------------------------------------------

    async def react_to_user_post(self, moment_id: int) -> int:
        """用户发了动态：最先来的那位马上评论，其余的按计划陆续到（推进时写）。
        谁来、几条按 ``plan_comments``（看和用户的亲疏）。"""
        moment = self.rt.repo.moment(moment_id)
        if moment is None:
            return 0
        plans = self.plans()
        plan = plans.get(str(moment_id)) or self.plan_comments(moment)
        members = {m.id: m for m in self.hub.everyone()}
        n = 0
        first = next((e for e in plan if not e.get("done")), None)
        if first is not None and first["m"] in members:
            first["done"] = True
            done = await self.comment(members[first["m"]], moment)
            if done is None:
                first.pop("done")
            n += bool(done)
            if done:
                follow = self.plan_reply(moment, done)
                if follow is not None:
                    plan.append(follow)
        plans[str(moment_id)] = plan
        self.save_plans(plans)
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
