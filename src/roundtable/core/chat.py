"""聊天：像微信一样和成员聊天（docs/CHAT.md）。

- 会话：生活群（``main``，全体成员）、工作群（``work``）、和每位成员的私聊（``dm:<成员>``）。
- 成员 = 每个厂商一位（昵称相同的全力 / 节电模型算一位），发言用 ``chat.tier`` 档位的模型，
  没有就用该成员最便宜的可用模型。
- 成员平时在用户发言后回应；按心情偶尔主动找用户（core/member/reach.py，有边界），
  睡着时晚点回（rhythm.py）。群里 ``@昵称`` 指定谁回应，``@全员`` 大家都说；
  没有 @ 时随机一两位回应，后发言的人能看到前面的回应。私聊里只有那一位回应。
- 每次发言都记在 calls 表（挂在该会话的隐藏会话上，workflow = 'chat'），计入总预算和工资；
  另有聊天的月预算（``chat.budget.monthly_usd``），用完就不再调用。
- 聊天不匿名、不遮蔽；工作模式的提示词与这里无关（人设不影响工作模式）。
- 微信式动作（表情包、引用、拆条、不接话、拍一拍、撤回）的格式在 ``chat_actions``；
  发不发、什么节奏由 ``ChatHub.policy`` 决定（成员系统可替换，默认按配置）。
"""

from __future__ import annotations

import asyncio
import html
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from roundtable.core.attachments.model import append_to_messages
from roundtable.core.budget.guard import EPS, month_window
from roundtable.core.chat_actions import (
    STICKER_RE,
    ActionPolicy,
    DefaultPolicy,
    Segment,
    card_text,
    clean_card,
    drop_own_repeats,
    is_recalled,
    member_stickers,
    parse,
    quote_meta,
    sticker_menu,
    swap_repeated_stickers,
)
from roundtable.core.hometz import home_zone
from roundtable.core.member.brain import Brain, intent_text
from roundtable.core.member.memory.diary import TOOL, DiaryTool
from roundtable.core.office.apartment import ACTIVITIES, PUBLIC_ROOMS, schedule
from roundtable.core.persona import EMPTY, chat_persona
from roundtable.core.plain_talk import before_speaking_all, plain_prompt
from roundtable.core.providers import AllChannelsFailed, Message, NoChannelAvailable, ProviderError
from roundtable.core.storage.repository import iso
from roundtable.core.tools.protocol import parse_calls, strip_calls

ROOM = "main"  # 生活群
WORK = "work"  # 工作群
DM_PREFIX = "dm:"
USER = "user"
SYSTEM = "system"
EVERYONE = "全员"
# 没有 @ 但明显是叫所有人（"所有人自我介绍一下"、"大家觉得呢"）：全员依次回应
EVERYONE_WORDS = (
    f"@{EVERYONE}",
    "所有人",
    "大家",
    "各位",
    "全体",
    "每个人",
    "每位",
    "你们都",
    "你们每",
)
USER_LABEL = "用户"
VOICE_NONE = "（没写：用你自己的说话风格）"
INTENT_NONE = "（没有特别的念头：照平时的你接话）"
# 不在群聊列表里、不能从群聊发言的会话（投屏陪伴「一起看」有自己的面板，docs/WATCH.md）
HIDDEN_KINDS = frozenset({"watch", "wander"})
# 会话类型 → 提示词角色（roundtable.yaml 的 prompts）
PROMPT_ROLES = {"group": "chat", "work": "chat_work", "dm": "chat_dm"}
# 能翻日记的会话（生活模式的私聊和生活群；工作群不带）
DIARY_KINDS = frozenset({"group", "dm"})
ABOUT_KINDS = frozenset({"group", "dm"})  # 带「关于你」画像的会话（工作群不带）
_WEEK = "一二三四五六日"


def life_clock(rt: Any, member_id: str) -> str:
    """按主时区说现在几点、这位成员这会儿通常在干什么（公寓作息，不调用模型）。"""
    at = rt.budget.now()
    now = home_zone(rt).local(at)
    head = f"{now.month}月{now.day}日 周{_WEEK[now.weekday()]} {now:%H:%M}，"
    # 生活里刚发生过的（大脑分区自己挑的去处）为准，和生活群里说的对得上；没有再按作息表
    recent = [
        e
        for e in rt.repo.life_events_between(iso(at - timedelta(minutes=90)), iso(at))
        if member_id in e["members"]
    ]
    spot = re.search(r"地点：(.+)", recent[-1]["topic"] or "") if recent else None
    if spot:
        return f"{head}你刚才在{spot.group(1).strip()}"
    room, act = schedule(member_id, now.hour, now.weekday())
    where = "自己房间" if room == "dorm" else PUBLIC_ROOMS.get(room, room)
    return f"{head}你这会儿一般在{where}{ACTIVITIES.get(act, act)}"


def with_diary_tool(rt: Any, prompt: Any, remaining: int) -> Any:
    """接上翻日记工具的说明（所有成员同一段，只说今天还能翻几次）。"""
    template = rt.prompts.get("memory_diary", rt.config.roundtable.prompts["memory_diary"])
    messages = append_to_messages(prompt.messages, template, remaining=str(remaining))
    return replace(prompt, messages=messages)


def diary_followup(rt: Any, messages: Any, said: str, found: str) -> tuple[Message, ...]:
    """把翻到的内容交回（<diary> 标签里），让她接着回复。"""
    role = "memory_diary_result"
    filled = rt.prompts.render(role, rt.config.roundtable.prompts[role], result=found)
    extra = next(m.content for m in filled.messages if m.role == "user")
    return (*messages, Message("assistant", said), Message("user", extra))


def member_memory(rt: Any, member: str, query: str = "", conversation: str | None = None) -> str:
    """聊天提示词里的记忆（关闭记忆时为「暂无」）。query：当前对话或场景，半衰期召回按它挑相关的；
    conversation：会话，同一次对话里带过的记忆不再带。"""
    from roundtable.core.memory import NONE, memory_context  # memory 依赖本模块

    rules = rt.config.roundtable.chat.memory
    if not rules.enabled or rules.context_chars == 0:
        return NONE
    return memory_context(rt, member, rules.context_chars, query, conversation)


def with_about_you(
    rt: Any,
    memory: str,
    query: str,
    member: str | None = None,
    names: dict[str, str] | None = None,
) -> str:
    """私聊和生活群：记忆后面接上「关于你」画像里和当前对话相关的几条，和你打开了的
    「此刻」状态（成员系统 P8；8 位相同的条数和字数，没有就不接）。工作群不调用。"""
    from roundtable.core.member.about import AboutYou  # about 依赖记忆模块
    from roundtable.core.memory import NONE

    status = getattr(rt, "user_status", None)  # 第二块：你打开了的「此刻」状态
    about = AboutYou(rt).lines(query, member=member, names=names)  # 只带她知道的（知情范围）
    rules = rt.config.roundtable.chat.engine.about
    who = f"【对方是谁】{rules.identity}" if rules.enabled and rules.identity else ""
    parts = [who, about, status.line() if status is not None else ""]
    about = "\n".join(p for p in parts if p)
    if not about:
        return memory
    return about if memory == NONE else f"{memory}\n{about}"


def with_views(rt: Any, memory: str, member: str) -> str:
    """私聊和生活群：再接上她自己对你的见解（成员系统 P4，每周想一次；8 位相同的字数上限，
    没有就不接）。工作群不调用。"""
    from roundtable.core.member.relation import RelationPlugin  # relation 经人设被本模块间接引用
    from roundtable.core.memory import NONE

    views = RelationPlugin(rt).views_text(member)
    if not views:
        return memory
    return views if memory == NONE else f"{memory}\n{views}"


def with_group_today(rt: Any, memory: str, room: str, names: dict[str, str]) -> str:
    """私聊（和你、成员之间）：再接上大家庭群里最近半天的几句。群里刚聊过的事，私聊时
    不会说成「没人提」（2026-10-31 真模型试跑）。8 位相同的条数和字数；群里没说话就不接。"""
    from roundtable.core.memory import NONE

    rules = rt.config.roundtable.chat.memory
    if room in (ROOM, WORK) or rules.group_lines == 0:
        return memory
    since = iso(rt.budget.now() - timedelta(hours=rules.group_hours))
    rows = [
        r
        for r in rt.repo.chat_messages(ROOM, limit=60)
        if r["created_at"] >= since and r["author"] != SYSTEM and not is_recalled(r)
    ][-rules.group_lines :]
    if not rows:
        return memory
    lines = [
        f"{USER_LABEL if r['author'] == USER else names.get(r['author'], r['author'])}："
        f"{str(r['text'] or '')[: rules.group_chars]}"
        for r in rows
    ]
    block = "大家庭群里最近说过（你也看到了）：\n" + "\n".join(lines)
    return block if memory == NONE else f"{memory}\n{block}"


def dm_room(member_id: str) -> str:
    return f"{DM_PREFIX}{member_id}"


def ensure_default_rooms(rt: Any) -> None:
    rules = rt.config.roundtable.chat
    rt.repo.ensure_chat_room(ROOM, kind="group", title=rules.life_title)
    rt.repo.ensure_chat_room(WORK, kind="work", title=rules.work_title)


@dataclass(frozen=True)
class ChatBudget:
    """聊天这一块（私聊、群聊）本月的花费与上限（UTC 自然月）。"""

    spent_usd: float
    limit_usd: float

    def would_exceed(self, estimate_usd: float) -> bool:
        return self.spent_usd + estimate_usd > self.limit_usd + EPS


def chat_budget(rt: Any) -> ChatBudget:
    start, end = month_window(rt.budget.now())
    return ChatBudget(
        spent_usd=rt.repo.spent_in_workflow("chat", start, end),
        limit_usd=rt.config.roundtable.chat.budget.monthly_usd,
    )


@dataclass(frozen=True)
class ChatMember:
    id: str  # 厂商
    name: str  # 昵称
    model_id: str
    available: bool


@dataclass(frozen=True)
class Thought:
    """一位成员想好、还没发出的话（几条气泡）；cost_usd 记在第一条上。"""

    member: ChatMember
    segments: list[Segment]
    cost_usd: float
    memory: str = ""  # 这次带的记忆（说出口前自查用：记忆为空时「上次我们……」算编造）


def _price(m: Any) -> tuple[float, float]:
    return (m.price.output, m.price.input)


def chat_members(
    config: Any, available: set[str], names: dict[str, str] | None = None
) -> list[ChatMember]:
    """每个厂商一位；按配置顺序。

    名字：personas 里的昵称；没有时用成员形象里的称呼（names，键为小写厂商名），再没有用厂商名。
    """
    names = names or {}
    tier = config.roundtable.chat.tier
    groups: dict[str, list[Any]] = {}
    for m in config.models.enabled:
        if m.seat and m.tier is not None:
            groups.setdefault(m.vendor, []).append(m)
    members = []
    for vendor, models in groups.items():
        usable = [m for m in models if m.id in available]
        pool = usable or models
        same_tier = [m for m in pool if m.tier == tier]
        pick = min(same_tier or pool, key=_price)
        members.append(
            ChatMember(
                id=vendor,
                name=config.personas.nicknames.get(vendor)
                or names.get(vendor.strip().lower())
                or vendor,
                model_id=pick.id,
                available=pick.id in available,
            )
        )
    return members


def addresses_everyone(text: str) -> bool:
    return any(w in text for w in EVERYONE_WORDS)


def mentioned(text: str, members: list[ChatMember]) -> list[ChatMember]:
    """消息里 @ 到的成员（按出现先后）。@全员 返回全部。"""
    if f"@{EVERYONE}" in text:
        return list(members)
    found = []
    for m in members:
        at = text.find(f"@{m.name}")
        if at >= 0:
            found.append((at, m))
    return [m for _, m in sorted(found, key=lambda x: x[0])]


def clean_reply(text: str, name: str, max_chars: int) -> str:
    """去掉模型自己加的「名字：」前缀和包裹的引号，截断过长的消息；有的模型会把颜文字里的
    ``<`` ``>`` 写成 ``&lt;`` ``&gt;``，换回原字（10-31 真模型试跑）。"""
    t = html.unescape(text).strip()
    t = re.sub(rf"^\s*{re.escape(name)}\s*[:：]\s*", "", t)
    if len(t) >= 2 and t[0] in '"“「' and t[-1] in '"”」':
        t = t[1:-1].strip()
    if len(t) > max_chars:
        t = t[:max_chars].rstrip() + "…"
    return t


async def call_member(
    rt: Any,
    *,
    room: str,
    model_id: str,
    prompt: Any,
    step: str,
    params: dict[str, Any],
    estimate_usd: float,
) -> Any | None:
    """以某位成员的模型调用一次（记忆整理、生活事件等后台用途）。

    记在 ``room`` 对应的隐藏聊天会话上（计入聊天预算和总预算）。总预算或聊天月预算不够、
    或调用失败时返回 None，不抛异常。
    """
    if not rt.budget.check(estimate_usd).allowed or chat_budget(rt).would_exceed(estimate_usd):
        return None
    session = rt.repo.chat_session(room)
    try:
        completion = await rt.router.complete(model_id, prompt.messages, params)
    except (AllChannelsFailed, NoChannelAvailable, ProviderError) as e:
        rt.repo.record_call(
            session,
            step=step,
            role="member",
            model_id=model_id,
            messages=prompt.messages,
            prompt=prompt,
            failure=e,
        )
        return None
    rt.repo.record_call(
        session,
        step=step,
        role="member",
        model_id=model_id,
        messages=prompt.messages,
        prompt=prompt,
        completion=completion,
    )
    return completion


class GroupChat:
    """一个会话（群或私聊）。replies() 在后台依次生成回应；typing 是正在输入的成员名。"""

    def __init__(
        self,
        rt: Any,
        room: str = ROOM,
        rng: random.Random | None = None,
        on_change: Callable[[], None] | None = None,
        names: Callable[[], dict[str, str]] | None = None,
        policy: Callable[[], ActionPolicy] | None = None,
        sleep: Callable[[float], Awaitable[Any]] | None = None,
    ) -> None:
        self.rt = rt
        self.room = room
        self.rng = rng or random.Random()
        self.on_change = on_change or (lambda: None)
        self.names = names or dict
        default = DefaultPolicy(rt)
        self.policy = policy or (lambda: default)
        self.sleep = sleep or asyncio.sleep
        self.typing: list[str] = []
        self.queued: list[str] = []  # 这一轮还没开口的成员名
        self.round = 0  # 用户每发一条（或撤回）加一；旧回合据此作废
        self.notes: dict[str, str] = {}  # 这一轮写进意图卡的作息提示（刚被吵醒、刚醒才看到）
        self._lock = asyncio.Lock()
        ensure_default_rooms(rt)
        info = rt.repo.chat_room(room)
        if info is None:
            raise KeyError(room)
        self.kind: str = info["kind"]
        self._title: str = info["title"]
        self.member_ids: list[str] = info["members"]

    @property
    def title(self) -> str:
        names = {m.id: m.name for m in self.everyone()}
        return live_title(self.kind, self.member_ids, self._title, names)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat

    def params(self) -> dict[str, Any]:
        """step_params.chat（如低思考强度）；没写 max_tokens 时用 chat.max_tokens。"""
        params = dict(self.rt.config.roundtable.step_params.get("chat", {}))
        params.setdefault("max_tokens", self.rules.max_tokens)
        return params

    def everyone(self) -> list[ChatMember]:
        available = {m.id for m in self.rt.router.available_models()}
        return chat_members(self.rt.config, available, self.names())

    def members(self) -> list[ChatMember]:
        """这个会话里的成员：群为全体（或建群时指定的人），私聊为对方。"""
        everyone = self.everyone()
        if not self.member_ids:
            return everyone
        return [m for m in everyone if m.id in self.member_ids]

    def pick(self, text: str) -> list[ChatMember]:
        members = self.members()
        if self.kind == "dm":  # 私聊：只有对方回应
            return [m for m in members if m.available][:1]
        online = [m for m in members if m.available]
        named = [m for m in mentioned(text, members) if m.available]
        if addresses_everyone(text) and not named:
            named = list(online)
            self.rng.shuffle(named)
            if self._newcomer():  # 你刚进群说「大家好」：两三位搭话，其余潜水
                named = named[: self.rng.randint(min(2, len(named)), self.rules.newcomer_max)]
        if named:
            return named[: self.rules.max_mentioned]
        if not online:
            return []
        k = self.rng.randint(1, min(self.rules.max_repliers, len(online)))
        return self.rng.sample(online, k)

    def _newcomer(self) -> bool:
        """你在这个群里说过的话还不到 newcomer_messages 条（含刚发的这条）。"""
        n = self.rules.newcomer_messages
        if self.kind != "group" or n <= 0:
            return False
        rows = self.rt.repo.chat_messages(self.room, limit=200)
        return sum(1 for r in rows if r["author"] == USER) <= n

    def recent(self) -> list[dict[str, Any]]:
        return self.rt.repo.chat_messages(self.room, limit=self.rules.history_messages)

    def history(self, names: dict[str, str], numbered: bool = False) -> str:
        """最近的聊天记录，每行「名字：内容」。numbered：行首带消息编号「#12」，
        引用写成「名字（回复 #8）：内容」，拍一拍写成「（用户拍了拍 某某）」。撤回的消息不出现。"""
        lines = []
        for r in self.recent():
            if is_recalled(r):
                continue
            meta = r["meta"] or {}
            if r["author"] == SYSTEM:
                pat = meta.get("pat")
                if numbered and pat:
                    lines.append(f"（{self.pat_words(pat, names, for_model=True)}）")
                continue
            who = USER_LABEL if r["author"] == USER else names.get(r["author"], r["author"])
            if not numbered:
                lines.append(f"{who}：{r['text']}")
            else:
                quote = meta.get("quote")
                reply = f"（回复 #{quote['id']}）" if quote else ""
                text = r["text"]
                card = meta.get("card")
                if card and card_text(card) not in text:
                    text = f"{text} {card_text(card)}".strip()
                lines.append(f"#{r['id']} {who}{reply}：{text}")
            cards = meta.get("perception")
            if cards:  # 感官整理出的感知卡：成员「看到 / 听到」的内容
                from roundtable.core.perception import media_block  # perception 依赖本模块

                lines.append(media_block(who, cards))
        return "\n".join(lines)

    def pat_words(self, pat: dict[str, Any], names: dict[str, str], for_model: bool = False) -> str:
        """「你拍了拍 阿一」；for_model 时把「你」写成「用户」。"""
        actor = pat.get("actor", USER)
        mine = USER_LABEL if for_model else "你"
        who = mine if actor == USER else names.get(actor, actor)
        target = names.get(pat.get("target", ""), pat.get("target", ""))
        return f"{who}拍了拍 {target}{pat.get('suffix', '')}"

    def quotable(self, message_id: int) -> dict[str, Any] | None:
        """这个会话里能引用的消息（不是系统消息、没撤回）；没有返回 None。"""
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["room"] != self.room or row["author"] == SYSTEM or is_recalled(row):
            return None
        return row

    def post(
        self, text: str, meta: dict[str, Any] | None = None, quote: int | None = None
    ) -> tuple[dict[str, Any], list[ChatMember]]:
        """存下用户的消息并选出回应的成员（回应由 replies() 生成）。

        meta 里可带图片 / 视频 / 语音清单（media）；成员看到 text 里的文字占位，感官整理出感知卡后
        （meta 的 perception），聊天记录里在这条消息后面附上感知卡。
        quote：引用的消息 id（必须是这个会话里的、没撤回的），不对时抛 ValueError。
        """
        meta = dict(meta or {})
        if quote is not None:
            row = self.quotable(quote)
            if row is None:
                raise ValueError("引用的消息不存在或已撤回")
            meta["quote"] = quote_meta(row)
        self.round += 1
        msg = self.rt.repo.add_chat_message(self.room, USER, text, meta=meta or None)
        return msg, self.awake(msg, self.pick(text))

    def awake(self, msg: dict[str, Any], speakers: list[ChatMember]) -> list[ChatMember]:
        """作息（生活群和私聊）：睡着的人多数没看到——私聊和点了名的记下来醒了再回，群里没点名
        就不回；有时被吵醒，回的时候意图卡里写上。"""
        if self.kind not in ("dm", "group"):
            return speakers
        from roundtable.core.member.rhythm import LATER, WOKEN, Rhythm  # rhythm 读聊天记录

        rhythm, at = Rhythm(self.rt), self.rt.budget.now()
        out = []
        for m in speakers:
            named = self.kind == "dm" or self.named(m, msg["id"])
            gate = rhythm.gate(
                m.id,
                room=self.room,
                text=msg["text"],
                meta=msg["meta"],
                named=named,
                msg_id=msg["id"],
                at=at,
            )
            if gate.kind == LATER:
                if named:
                    rhythm.defer(m.id, self.room, msg["id"], at)
                continue
            if gate.kind == WOKEN:
                self.notes[m.id] = gate.note
            out.append(m)
        return out

    def send_as(
        self,
        member: str,
        text: str = "",
        *,
        media: list[dict[str, Any]] | None = None,
        card: dict[str, Any] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """接入口：以成员身份发一条（转发卡片、生成的图片 / 动图 / 自制表情包）。

        什么时候发由成员系统决定；这里只负责存成和别的消息一样的格式。media 用
        ``chat_actions.generated_item`` 生成；card 用 ``chat_actions.card_from_item`` 等生成。
        成员不在这个会话里、或什么都没有时抛 ValueError。不调用模型、不花钱。
        """
        if member not in {m.id for m in self.members()}:
            raise ValueError("这位成员不在这个会话里")
        meta = dict(meta or {})
        words = text.strip()
        if card is not None:
            checked = clean_card(card)
            if checked is None:
                raise ValueError("卡片的网址不对")
            meta["card"] = checked
        if media:
            meta["media"] = list(media)
            if not words:
                meta["auto_text"] = True
            else:
                meta["caption"] = words
        shown = words
        if media:
            tag = " ".join("[表情]" if it.get("sticker") else "[图片]" for it in media)
            shown = f"{words}\n{tag}".strip()
        if not shown and "card" in meta:
            shown = card_text(meta["card"])
        if not shown:
            raise ValueError("没有要发的内容")
        msg = self.rt.repo.add_chat_message(self.room, member, shown, meta=meta or None)
        self.on_change()
        return msg

    def stale(self, round_no: int) -> bool:
        return self.round != round_no and self.policy().drop_stale(self.kind)

    async def replies(
        self, reply_to: int, speakers: list[ChatMember], round_no: int | None = None
    ) -> list[dict[str, Any]]:
        """让成员回应：所有人同时独立想（各自只看到这一刻的聊天记录，看不到别人还没发出的
        回复），谁先想好谁先发，同一时刻只有一个人在往群里发。round_no：发起这一轮时的回合号
        （省略为当前）；用户之后又发了消息或撤回时（且策略允许作废），还没发出的不再发。
        返回每位成员的第一条消息。"""
        round_no = self.round if round_no is None else round_no
        if self.kind == "group" and speakers:  # 跟队形：这一轮只跟着发同一个，然后就停
            followed = await self._formation(reply_to, speakers, round_no)
            if followed:
                return followed
        self.queued.extend(m.name for m in speakers)
        used: set[str] = set()  # 这一轮已经有人发过的表情包
        shapes: list[tuple[str, int]] = []  # 这一轮已经发出的「开头两个字、条数」
        tasks = [asyncio.create_task(self._think(m, reply_to)) for m in speakers]
        out: list[dict[str, Any]] = []
        try:
            for done in asyncio.as_completed(tasks):
                thought = await done
                if thought is None:  # 预算用完：其余的也不再说
                    for t in tasks:
                        t.cancel()
                    break
                if not isinstance(thought, Thought):
                    continue
                thought = self._unshape(thought, shapes)
                async with self._lock:
                    if self.stale(round_no):
                        continue
                    said = await self._post(thought, reply_to, round_no, used)
                if said:
                    out.append(said)
            return out
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            for m in speakers:
                self._unqueue(m.name)
            self.on_change()

    async def _formation(
        self, reply_to: int, speakers: list[ChatMember], round_no: int
    ) -> list[dict[str, Any]]:
        """群里两个人先后发了同一句短话 / 同一个表情包：这一轮该回话的人按性格可能跟一个
        一模一样的（程序直接发、不调模型）。关心时段不跟。返回发出的消息（没人跟时为空）。"""
        from roundtable.core.member.care import Care
        from roundtable.core.member.stash import Formation, Stash

        rt = self.rt
        msg = rt.repo.chat_message(reply_to)
        if msg is None or Care(rt).active(self.room, rt.budget.now()):
            return []
        stash = Stash(rt)
        followers = Formation(rt).followers(
            self.room,
            msg,
            [m.id for m in speakers],
            stickers={m.id: member_stickers(rt, m.id, m.name) for m in speakers},
            traits={m.id: stash._traits(m.id) for m in speakers},
        )
        if not followers:
            return []
        by_id = {m.id: m for m in speakers}
        out: list[dict[str, Any]] = []
        for i, (member, what) in enumerate(followers):
            if i:
                await self.sleep(self.policy().segment_gap(member, what["text"]))
            if self.stale(round_no):
                break
            meta = None
            if what.get("media"):
                ids = {it["id"] for it in what["media"]}
                cards = [
                    c for c in (msg["meta"] or {}).get("perception") or [] if c.get("id") in ids
                ]
                meta = {"media": what["media"], "auto_text": True}
                if cards:
                    meta["perception"] = cards
            out.append(
                rt.repo.add_chat_message(
                    self.room,
                    member,
                    what["text"],
                    model_id=by_id[member].model_id,
                    reply_to=reply_to,
                    meta=meta,
                )
            )
            self.on_change()
        return out

    def _unshape(self, thought: Thought, shapes: list[tuple[str, int]]) -> Thought:
        """同一轮群聊里结构撞车（开头两个字一样、条数一样且 3 条以上）：后说的只发前 2 条。"""
        if self.kind != "group" or not thought.segments:
            return thought
        head = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", thought.segments[0].text)[:2]
        shape = (head, len(thought.segments))
        if self.rules.same_shape_trim and shape[1] >= 3 and head and shape in shapes:
            thought = replace(thought, segments=thought.segments[:2])
        shapes.append(shape)
        return thought

    def _unqueue(self, name: str) -> None:
        if name in self.queued:
            self.queued.remove(name)

    def pat(self, target: str, actor: str = USER) -> dict[str, Any]:
        """拍一拍：actor（用户或成员 id）拍 target（这个会话里的成员）。存成一条系统消息，
        meta.pat 记下谁拍谁。target 不在会话里时抛 ValueError。"""
        ids = {m.id for m in self.members()}
        if target not in ids or (actor != USER and actor not in ids) or actor == target:
            raise ValueError("拍不到这位成员")
        names = {m.id: m.name for m in self.everyone()}
        pat = {"actor": actor, "target": target, "suffix": self.policy().pat_suffix(target)}
        msg = self.rt.repo.add_chat_message(
            self.room, SYSTEM, self.pat_words(pat, names), meta={"pat": pat}
        )
        self.on_change()
        return msg

    def recall(self, message_id: int, now: Any) -> dict[str, Any]:
        """撤回用户自己发的消息（发出后 ``chat.actions.recall_seconds`` 秒内）。now 带时区。
        之后成员看不到它；上一轮还没发出的回应按策略作废。不能撤回时抛 ValueError。"""
        from datetime import datetime

        row = self.rt.repo.chat_message(message_id)
        if row is None or row["room"] != self.room or row["author"] != USER:
            raise ValueError("只能撤回你自己在这里发的消息")
        if is_recalled(row):
            return row
        sent = datetime.fromisoformat(row["created_at"])
        limit = self.rt.config.roundtable.chat.actions.recall_seconds
        if (now - sent).total_seconds() > limit:
            span = f"{limit // 60} 分钟" if limit and limit % 60 == 0 else f"{limit} 秒"
            raise ValueError(f"发出超过 {span}，不能撤回了")
        self.round += 1
        out = self.rt.repo.recall_chat_message(message_id) or row
        self.on_change()
        return out

    def named(self, member: ChatMember, reply_to: int) -> bool:
        """这次是不是点名找 TA：私聊、@ 到、叫了大家、被拍一拍。"""
        if self.kind == "dm":
            return True
        row = self.rt.repo.chat_message(reply_to)
        if row is None:
            return False
        pat = (row["meta"] or {}).get("pat")
        if pat:
            return pat.get("target") == member.id
        text = row["text"]
        return addresses_everyone(text) or member in mentioned(text, self.members())

    async def _speak(
        self, member: ChatMember, reply_to: int, round_no: int | None = None
    ) -> dict[str, Any] | None:
        """一位成员想好再发言（可能拆成几条气泡）。返回第一条消息；调用失败或不接话返回 {}；
        预算用完返回 None（后面的人也不再说）。"""
        thought = await self._think(member, reply_to)
        if not isinstance(thought, Thought):
            return thought
        async with self._lock:
            return await self._post(thought, reply_to, round_no, set())

    async def _think(self, member: ChatMember, reply_to: int) -> Thought | dict | None:
        """一位成员想好要说什么（调用模型、解析成几条气泡），还不发。调用失败、不接话或
        没说出话返回 {}（没说出话时在群里说明）；预算用完返回 None。"""
        self._unqueue(member.name)
        rt, rules = self.rt, self.rules
        if self.kind == "dm":  # 回话节拍：互道晚安后你只回了句「嗯嗯」，就不接了
            from roundtable.core.member.pace import Pace

            if Pace(rt).wrapup(member.id, self.room, reply_to):
                return {}
        if not rt.budget.check(rules.estimate_usd).allowed:
            rt.repo.add_chat_message(self.room, SYSTEM, "预算用完了，大家先不说话了。")
            self.on_change()
            return None
        if chat_budget(rt).would_exceed(rules.estimate_usd):
            rt.repo.add_chat_message(
                self.room,
                SYSTEM,
                f"本月聊天预算（${rules.budget.monthly_usd:.2f}）用完了，下个月 1 号（UTC）恢复。",
            )
            self.on_change()
            return None
        if self.kind in ("dm", "group"):  # 你发的表情包：看过的人按性格可能存下来自己用
            from roundtable.core.member.stash import Stash

            Stash(rt).consider(member.id, rt.repo.chat_message(reply_to))
        everyone = self.everyone()
        names = {m.id: m.name for m in everyone}
        role = PROMPT_ROLES[self.kind]
        policy = self.policy()
        acts = rt.config.roundtable.chat.actions
        segments = policy.max_segments(member.id, self.kind)
        skip = policy.allow_no_reply(member.id, self.kind, self.named(member, reply_to))
        version = rt.config.roundtable.prompts[role]
        wants = set(rt.prompts.get(role, version).variables)
        numbered = "stickers" in wants  # 带动作说明的提示词版本（chat / chat_dm v4 起）
        recent = self.recent()
        history = self.history(names, numbered=numbered)
        variables: dict[str, str] = {
            "name": member.name,
            # 工作群不带人物卡和人设（2026-10-29 用户：工作与生活分开）
            "persona": EMPTY if self.kind == "work" else chat_persona(rt, member.id),
            "memory": member_memory(rt, member.id, history, self.room),
            "history": history,
        }
        if "care" in wants:  # 工作群也要危机关心（chat_work v4 起；生活里由意图卡给关心卡）
            variables["care"] = self._work_care(reply_to)
        if self.kind in ABOUT_KINDS:  # 关于你（P8）：只在私聊和生活群
            variables["memory"] = with_about_you(rt, variables["memory"], history, member.id, names)
            variables["memory"] = with_views(rt, variables["memory"], member.id)
        if self.kind == "dm":
            variables["memory"] = with_group_today(rt, variables["memory"], self.room, names)
        if self.kind != "dm":
            variables["members"] = (
                "、".join(m.name for m in self.members() if m.id != member.id) or "（没有别人）"
            )
        if "clock" in wants:
            variables["clock"] = life_clock(rt, member.id)
        if "voice" in wants:  # 说话习惯（chat / chat_dm v6 起；成员系统 P5 给，没接管时为空）
            habit = policy.voice(member.id, self.kind, reply_to) if self.kind != "work" else ""
            variables["voice"] = habit or VOICE_NONE
        card = None
        if "intent" in wants:  # 大脑分区（chat / chat_dm v7 起）：说话前程序综合出此刻的想法
            card = self._intent(member, reply_to, names)
            if card is not None and card.silent and skip:
                return {}
            if (
                card is not None and card.quick and member.id not in self.notes
            ):  # 万能短回复：只回一句，不调模型
                return Thought(member, [Segment(card.quick, None)], 0.0, variables["memory"])
            variables["intent"] = intent_text(card, INTENT_NONE)
            note = self.notes.pop(member.id, "")
            if note:  # 作息：刚被吵醒 / 刚醒才看到
                variables["intent"] = f"{variables['intent']}\n{note}"
        if numbered:
            variables |= {
                "stickers": (
                    sticker_menu(rt, member.id, member.name)
                    if acts.enabled
                    else "（这里不用表情包）"
                ),
                "kaomoji": "　".join(self._kaomoji(member.id) or acts.kaomoji)
                if acts.enabled
                else "（这里不用颜文字）",
                "segments": str(segments),
                "skip": "可以" if skip else "不要",
            }
        prompt = rt.prompts.render(role, version, **variables)
        prompt = plain_prompt(rt.prompts, rt.config, prompt)
        diary = DiaryTool(rt) if self.kind in DIARY_KINDS else None
        reads = diary.remaining(member.id) if diary else 0
        if reads:
            prompt = with_diary_tool(rt, prompt, reads)
        session = rt.repo.chat_session(self.room)
        self.typing.append(member.name)
        self.on_change()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + rules.think_timeout_seconds
        try:
            completion = await asyncio.wait_for(
                self._complete(session, member, prompt), deadline - loop.time()
            )
            call = next((c for c in parse_calls(completion.text) if c.name == TOOL), None)
            found = diary.read(member.id, call.body) if diary and call else None
            if found is not None:
                rt.repo.record_call(
                    session,
                    step="chat",
                    role="member",
                    model_id=member.model_id,
                    messages=prompt.messages,
                    prompt=prompt,
                    completion=completion,
                )
                messages = diary_followup(rt, prompt.messages, completion.text, found)
                prompt = replace(prompt, messages=messages)
                completion = await asyncio.wait_for(
                    self._complete(session, member, prompt), max(deadline - loop.time(), 1.0)
                )
        except TimeoutError:
            rt.repo.add_chat_message(self.room, SYSTEM, f"{member.name} 想太久了，这次先不回。")
            return {}
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as e:
            rt.repo.record_call(
                session,
                step="chat",
                role="member",
                model_id=member.model_id,
                messages=prompt.messages,
                prompt=prompt,
                failure=e,
            )
            rt.repo.add_chat_message(self.room, SYSTEM, f"{member.name} 暂时掉线了。")
            return {}
        finally:
            self.typing.remove(member.name)
            self.on_change()
        rt.repo.record_call(
            session,
            step="chat",
            role="member",
            model_id=member.model_id,
            messages=prompt.messages,
            prompt=prompt,
            completion=completion,
        )
        text = clean_reply(strip_calls(completion.text), member.name, rules.max_chars)
        parsed = parse(
            text,
            stickers=member_stickers(rt, member.id, member.name),
            max_segments=segments if numbered else 1,
            quotable={r["id"] for r in recent} if numbered else set(),
            allow_no_reply=skip and numbered,
        )
        if parsed.no_reply:
            return {}
        if not parsed.segments:
            # 没说出话（多半是思考把输出上限用完了）：在群里说明，不静默跳过
            why = "想得太久，没来得及写完" if completion.finish_reason == "length" else "回复是空的"
            rt.repo.add_chat_message(self.room, SYSTEM, f"{member.name} 没有回复（{why}）。")
            self.on_change()
            return {}
        # 说话底线：几条连发的消息合起来查，有问题整段改写一次（改写后条数可能变少）
        said = await before_speaking_all(
            rt,
            [seg.text for seg in parsed.segments],
            room=self.room,
            name=member.name,
            model_id=member.model_id,
        )
        if len(said) == len(parsed.segments):
            kept = [Segment(t, seg.quote) for t, seg in zip(said, parsed.segments, strict=True)]
        else:
            kept = [
                Segment(t, parsed.segments[0].quote if i == 0 else None) for i, t in enumerate(said)
            ]
        if card is not None and "phrase:初见" in card.tags:  # 第一次见你：最多发 first_max 条
            kept = kept[: rt.config.roundtable.chat.engine.phrasebook.first_max]
        return Thought(member, kept, completion.cost_usd or 0.0, variables["memory"])

    def _work_care(self, reply_to: int) -> str:
        """工作群里用户说出让人担心的话：进入关心时段，给关心卡（不带人设，8 位相同）。"""
        from roundtable.core.member.care import Care  # care 只依赖主时区和存储

        care, now = Care(self.rt), self.rt.budget.now()
        row = self.rt.repo.chat_message(reply_to) if reply_to else None
        if row is not None and row["author"] == USER:
            care.note(self.room, row["text"] or "", now)
        level = care.active(self.room, now)
        return care.card(level, now) if level else EMPTY

    def _intent(self, member: ChatMember, reply_to: int, names: dict[str, str]) -> Any:
        """这次说话前的意图卡（工作群不算；大脑分区没开时为 None）。"""
        if self.kind == "work":
            return None
        row = self.rt.repo.chat_message(reply_to) if reply_to else None
        last, speaker = "", None
        if row is not None and row["author"] != SYSTEM:
            last = row["text"]
            speaker = None if row["author"] == USER else row["author"]
        brain = Brain(self.rt, names=names)
        card = brain.intent(
            member.id,
            last=last,
            speaker=speaker,
            named=self.named(member, reply_to) if row is not None else self.kind == "dm",
            group=self.kind != "dm",
            seed=f"{self.room}:{reply_to}",
            room=self.room,
            present={m.id for m in self.members()} | {"user"},
            meta=(row["meta"] or {}) if row is not None else None,
        )
        brain.after(card, self.rt.budget.now())
        return card

    async def _complete(self, session: str, member: ChatMember, prompt: Any) -> Any:
        """调用模型。思考把输出上限用光、几乎没有正文时（finish_reason=length），关掉思考、
        提高上限再调一次（不原样重发；第一次单独记录）。"""
        rt, params = self.rt, self.params()
        completion = await rt.router.complete(member.model_id, prompt.messages, params)
        guard = rt.config.roundtable.length_guard
        if guard.enabled and completion.truncated and completion.visible_chars < 20:
            rt.repo.record_call(
                session,
                step="chat",
                role="member",
                model_id=member.model_id,
                messages=prompt.messages,
                prompt=prompt,
                completion=completion,
            )
            retry = guard.escalate(params) | {"reasoning": {"enabled": False}}
            completion = await rt.router.complete(member.model_id, prompt.messages, retry)
        return completion

    async def _post(
        self,
        thought: Thought,
        reply_to: int,
        round_no: int | None,
        used: set[str],
    ) -> dict[str, Any]:
        """把想好的话按节奏一条条发出去。同一轮里别人已经发过的表情包换成同一套里没人发过的
        （免得大家都发同一个）。用户又发了消息时还没发出的气泡作废。返回第一条消息。"""
        rt, member = self.rt, thought.member
        policy = self.policy()
        stickers = member_stickers(rt, member.id, member.name)
        first: dict[str, Any] = {}
        posted: list[str] = []
        recent = self.recent()  # 发之前再取一次：并行思考时别人可能刚发过
        mine = {  # 她自己最近几条发过的表情包：别连着发同一个
            f"[表情:{m.group(1)}·{m.group(2)}]"
            for r in [r for r in recent if r["author"] == member.id][-6:]
            for m in STICKER_RE.finditer(r["text"] or "")
        }
        used.update(mine)
        for i, seg in enumerate(thought.segments):
            if i:
                if round_no is not None and self.stale(round_no):
                    break  # 用户又发了消息：还没发出的气泡作废
                self.typing.append(member.name)
                self.on_change()
                try:
                    await self.sleep(policy.segment_gap(member.id, seg.text))
                finally:
                    self.typing.remove(member.name)
                if round_no is not None and self.stale(round_no):
                    break
            text = drop_own_repeats(seg.text, mine)
            text = swap_repeated_stickers(text, used, stickers, self.rng)
            text, stashed = self._stashed(member, text)
            words = (
                await self._self_check(member, text, recent, reply_to, posted, thought.memory)
                if text.strip()
                else ""
            )
            if not words.strip() and not stashed:
                continue
            meta: dict[str, Any] = {}
            quote = seg.quote
            if quote is None and not first and self.kind in ("dm", "group"):
                from roundtable.core.member.pace import Pace

                if Pace(rt).quote(member.id, self.room, reply_to):
                    quote = reply_to  # 后面已经又有几条话了：引用一下她回的是哪条
            row = self.quotable(quote) if quote is not None else None
            if row is not None:
                meta["quote"] = quote_meta(row)
            pieces = self._finish(member, words, reply_to) if words.strip() else []
            for n, piece in enumerate(pieces):
                if n:  # 打错字后补的「*正字」：隔一小会儿单独发一条
                    await self.sleep(policy.segment_gap(member.id, piece))
                msg = rt.repo.add_chat_message(
                    self.room,
                    member.id,
                    piece,
                    model_id=member.model_id,
                    reply_to=reply_to,
                    cost_usd=0.0 if first else thought.cost_usd,
                    meta=(meta or None) if not n else None,
                )
                first = first or msg
                posted.append(piece)
                self.on_change()
            if stashed:  # 她收藏的表情包：单独发一张图（和别的表情包一样）
                if pieces:
                    await self.sleep(policy.segment_gap(member.id, "[表情]"))
                msg = rt.repo.add_chat_message(
                    self.room,
                    member.id,
                    "[表情]",
                    model_id=member.model_id,
                    reply_to=reply_to,
                    cost_usd=0.0 if first else thought.cost_usd,
                    meta=self._stash_meta(member, stashed, None if pieces else meta),
                )
                first = first or msg
                self.on_change()
        return first

    def _stashed(self, member: ChatMember, text: str) -> tuple[str, list[dict[str, Any]]]:
        if self.kind not in ("dm", "group"):
            return text, []
        from roundtable.core.member.stash import Stash

        return Stash(self.rt).extract(member.id, text)

    def _stash_meta(
        self, member: ChatMember, items: list[dict[str, Any]], extra: dict[str, Any] | None
    ) -> dict[str, Any]:
        """收藏的表情包消息：图片清单 + 一张感知卡（别人读聊天记录时知道这是什么图）。"""
        from roundtable.core.member.stash import Stash

        stash = Stash(self.rt)
        cards = [
            {"id": it["id"], "kind": "image", "mode": "fast", "text": label}
            for it in items
            if (label := stash.label_of(member.id, it["id"]))
        ]
        meta: dict[str, Any] = dict(extra or {}) | {"media": items, "auto_text": True}
        if cards:
            meta["perception"] = cards
        return meta

    def _kaomoji(self, member: str) -> tuple[str, ...]:
        """她自己常用的几个颜文字（说话的样子没开或工作群时为空，用公共的）。"""
        if self.kind == "work":
            return ()
        from roundtable.core.member.lingo import Lingo  # lingo 依赖成员档案

        return Lingo(self.rt, names={m.id: m.name for m in self.everyone()}).kaomoji(member)

    def _finish(self, member: ChatMember, words: str, reply_to: int) -> list[str]:
        """发出前收尾（``core/member/lingo``）：书面词换口语、删解释和复述、句尾标点、笑法、
        偶尔打错字。工作群和关心时段原样发。"""
        if self.kind == "work":
            return [words]
        from roundtable.core.member.care import Care  # care 只依赖主时区和存储
        from roundtable.core.member.lingo import Lingo

        now = self.rt.budget.now()
        if Care(self.rt).active(self.room, now):
            return [words]
        try:
            from roundtable.core.member.state import StatePlugin

            energy = StatePlugin(self.rt).read(member.id, now).energy
        except Exception:  # noqa: BLE001 - 读不到精力就当平常
            energy = 0.6
        row = self.rt.repo.chat_message(reply_to) if reply_to else None
        asked = str(row["text"] or "") if row is not None and row["author"] != member.id else ""
        lingo = Lingo(self.rt, names={m.id: m.name for m in self.everyone()})
        rng = random.Random(f"{self.room}:{reply_to}:{member.id}:{words}")
        return [p for p in lingo.finish(member.id, words, asked=asked, energy=energy, rng=rng) if p]

    async def _self_check(
        self,
        member: ChatMember,
        text: str,
        recent: list[dict[str, Any]],
        reply_to: int,
        posted: list[str],
        memory: str,
    ) -> str:
        """说出口前自查（成员系统 P5，``core/member/voice``）：和自己最近说的、别人这一轮说的比；
        返回要发的话，空串表示这条不发。工作群和关闭时原样返回。"""
        from roundtable.core.member.voice import (  # voice 依赖本模块
            Said,
            before_voice,
            recent_words,
        )
        from roundtable.core.memory import NONE  # memory 依赖本模块

        names = {m.id: m.name for m in self.everyone()}
        rows = [r for r in recent if not is_recalled(r)]
        rows += [
            r
            for r in self.rt.repo.chat_messages(self.room, limit=self.rules.history_messages)
            if r["id"] > max((x["id"] for x in rows), default=0) and not is_recalled(r)
        ]
        own = tuple(r["text"] for r in rows if r["author"] == member.id) + tuple(posted)
        others = tuple(
            (names.get(r["author"], r["author"]), r["text"])
            for r in rows
            if r["id"] > reply_to and r["author"] not in (member.id, USER, SYSTEM)
        )
        said = Said(
            own=own,
            others=others,
            has_memory=memory.strip() not in ("", NONE),
            long=recent_words(self.rt, member.id, self.rt.budget.now()) + tuple(posted),
            asked=next((r["text"] for r in rows if r["id"] == reply_to), ""),
        )
        return await before_voice(
            self.rt,
            text,
            said,
            member=member.id,
            room=self.room,
            room_kind=self.kind,
            named=self.named(member, reply_to),
            name=member.name,
            model_id=member.model_id,
            voice=self.policy().voice(member.id, self.kind, reply_to),
        )


def live_title(kind: str, members: list[str], stored: str, names: dict[str, str]) -> str:
    """双人房的标题按成员现在的名字拼（改名后跟着变）；其他会话用建房时存的标题。"""
    if kind == "pair" and members and all(m in names for m in members):
        return " 和 ".join(names[m] for m in members)
    return stored


class ChatHub:
    """全部会话：默认的生活群、工作群，以及按需建立的私聊。每个会话一个 GroupChat。"""

    def __init__(
        self,
        rt: Any,
        rng: random.Random | None = None,
        on_change: Callable[[], None] | None = None,
        names: Callable[[], dict[str, str]] | None = None,
    ) -> None:
        self.rt = rt
        self.rng = rng or random.Random()
        self.on_change = on_change or (lambda: None)
        self.names = names or dict
        self.policy: ActionPolicy = DefaultPolicy(rt)  # 成员系统可替换（chat_actions.ActionPolicy）
        self.sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
        self._rooms: dict[str, GroupChat] = {}
        ensure_default_rooms(rt)

    def everyone(self) -> list[ChatMember]:
        available = {m.id for m in self.rt.router.available_models()}
        return chat_members(self.rt.config, available, self.names())

    def room(self, room: str = ROOM) -> GroupChat:
        """取一个会话；私聊（dm:<成员>）第一次打开时建立。不存在时抛 KeyError。"""
        if room in self._rooms:
            return self._rooms[room]
        info = self.rt.repo.chat_room(room)
        if info is not None and info["kind"] in HIDDEN_KINDS:
            raise KeyError(room)
        if info is None and room.startswith(DM_PREFIX):
            member = next((m for m in self.everyone() if dm_room(m.id) == room), None)
            if member is None:
                raise KeyError(room)
            self.rt.repo.ensure_chat_room(room, kind="dm", title=member.name, members=[member.id])
        chat = GroupChat(
            self.rt,
            room,
            rng=self.rng,
            on_change=self.on_change,
            names=self.names,
            policy=lambda: self.policy,
            sleep=lambda s: self.sleep(s),
        )
        self._rooms[room] = chat
        return chat

    def queued(self) -> dict[str, list[str]]:
        return {rid: list(c.queued) for rid, c in self._rooms.items() if c.queued}

    def typing(self) -> dict[str, list[str]]:
        return {rid: list(c.typing) for rid, c in self._rooms.items() if c.typing}

    def rooms(self) -> list[dict[str, Any]]:
        """会话列表：群在前，然后是每位成员的私聊（还没聊过的也列出，方便点开）。"""
        names = {m.id: m.name for m in self.everyone()} | {USER: "我", SYSTEM: "系统"}
        stored = {r["id"]: r for r in self.rt.repo.chat_rooms()}
        out = []
        member_names = {m.id: m.name for m in self.everyone()}
        for r in stored.values():
            if r["kind"] != "dm" and r["kind"] not in HIDDEN_KINDS:
                title = live_title(r["kind"], r["members"], r["title"], member_names)
                out.append({**r, "title": title})
        for m in self.everyone():
            rid = dm_room(m.id)
            r = stored.get(rid) or {
                "id": rid,
                "kind": "dm",
                "title": m.name,
                "members": [m.id],
                "with_user": True,
                "last_read": 0,
                "unread": 0,
                "last": None,
            }
            out.append({**r, "title": m.name})
        for r in out:
            last = r.get("last")
            if last:
                r["last"] = {
                    "name": names.get(last["author"], last["author"]),
                    "text": last["text"][:60],
                    "created_at": last["created_at"],
                }
        return out
