"""聊天：像微信一样和成员聊天（docs/CHAT.md）。

- 会话：生活群（``main``，全体成员）、工作群（``work``）、和每位成员的私聊（``dm:<成员>``）。
- 成员 = 每个厂商一位（昵称相同的全力 / 节电模型算一位），发言用 ``chat.tier`` 档位的模型，
  没有就用该成员最便宜的可用模型。
- 成员不主动找用户：只在用户发言后回应。群里 ``@昵称`` 指定谁回应，``@全员`` 大家都说；
  没有 @ 时随机一两位回应，后发言的人能看到前面的回应。私聊里只有那一位回应。
- 每次发言都记在 calls 表（挂在该会话的隐藏会话上，workflow = 'chat'），计入总预算和工资；
  另有聊天的月预算（``chat.budget.monthly_usd``），用完就不再调用。
- 聊天不匿名、不遮蔽；工作模式的提示词与这里无关（人设不影响工作模式）。
- 微信式动作（表情包、引用、拆条、不接话、拍一拍、撤回）的格式在 ``chat_actions``；
  发不发、什么节奏由 ``ChatHub.policy`` 决定（成员系统可替换，默认按配置）。
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from roundtable.core.budget.guard import EPS, month_window
from roundtable.core.chat_actions import (
    ActionPolicy,
    DefaultPolicy,
    is_recalled,
    parse,
    quote_meta,
    sticker_list,
)
from roundtable.core.persona import EMPTY, chat_persona
from roundtable.core.plain_talk import before_speaking, plain_prompt
from roundtable.core.providers import AllChannelsFailed, NoChannelAvailable, ProviderError

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
# 会话类型 → 提示词角色（roundtable.yaml 的 prompts）
PROMPT_ROLES = {"group": "chat", "work": "chat_work", "dm": "chat_dm"}


def member_memory(rt: Any, member: str, query: str = "") -> str:
    """聊天提示词里的记忆（关闭记忆时为「暂无」）。query：当前对话或场景，半衰期召回按它挑相关的。"""
    from roundtable.core.memory import NONE, memory_context  # memory 依赖本模块

    rules = rt.config.roundtable.chat.memory
    if not rules.enabled or rules.context_chars == 0:
        return NONE
    return memory_context(rt, member, rules.context_chars, query)


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
    """去掉模型自己加的「名字：」前缀和包裹的引号，截断过长的消息。"""
    t = text.strip()
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
        self._lock = asyncio.Lock()
        ensure_default_rooms(rt)
        info = rt.repo.chat_room(room)
        if info is None:
            raise KeyError(room)
        self.kind: str = info["kind"]
        self.title: str = info["title"]
        self.member_ids: list[str] = info["members"]

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
        if named:
            return named[: self.rules.max_mentioned]
        if not online:
            return []
        k = self.rng.randint(1, min(self.rules.max_repliers, len(online)))
        return self.rng.sample(online, k)

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
                lines.append(f"#{r['id']} {who}{reply}：{r['text']}")
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
        return msg, self.pick(text)

    def stale(self, round_no: int) -> bool:
        return self.round != round_no and self.policy().drop_stale(self.kind)

    async def replies(
        self, reply_to: int, speakers: list[ChatMember], round_no: int | None = None
    ) -> list[dict[str, Any]]:
        """依次让成员回应。round_no：发起这一轮时的回合号（省略为当前）；用户之后又发了消息
        或撤回时（且策略允许作废），还没开口的人不再说。返回每位成员的第一条消息。"""
        round_no = self.round if round_no is None else round_no
        self.queued.extend(m.name for m in speakers)
        try:
            async with self._lock:
                out = []
                for member in speakers:
                    self._unqueue(member.name)
                    if self.stale(round_no):
                        continue
                    said = await self._speak(member, reply_to, round_no)
                    if said is None:
                        break
                    if said:
                        out.append(said)
                return out
        finally:
            for m in speakers:
                self._unqueue(m.name)
            self.on_change()

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
        """一位成员发言（可能拆成几条气泡）。返回第一条消息；调用失败或不接话返回 {}；
        预算用完返回 None（后面的人也不再说）。"""
        rt, rules = self.rt, self.rules
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
            "memory": member_memory(rt, member.id, history),
            "history": history,
        }
        if self.kind != "dm":
            variables["members"] = (
                "、".join(m.name for m in self.members() if m.id != member.id) or "（没有别人）"
            )
        if numbered:
            variables |= {
                "stickers": sticker_list(acts.stickers) if acts.enabled else "（这里不用表情包）",
                "kaomoji": "　".join(acts.kaomoji) if acts.enabled else "（这里不用颜文字）",
                "segments": str(segments),
                "skip": "可以" if skip else "不要",
            }
        prompt = rt.prompts.render(role, version, **variables)
        prompt = plain_prompt(rt.prompts, rt.config, prompt)
        session = rt.repo.chat_session(self.room)
        self.typing.append(member.name)
        self.on_change()
        try:
            completion = await rt.router.complete(member.model_id, prompt.messages, self.params())
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
        text = clean_reply(completion.text, member.name, rules.max_chars)
        parsed = parse(
            text,
            stickers=acts.stickers if acts.enabled else {},
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
        first: dict[str, Any] = {}
        for i, seg in enumerate(parsed.segments):
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
            words = await before_speaking(
                rt, seg.text, room=self.room, name=member.name, model_id=member.model_id
            )
            if not words.strip():
                continue
            meta: dict[str, Any] = {}
            row = self.quotable(seg.quote) if seg.quote is not None else None
            if row is not None:
                meta["quote"] = quote_meta(row)
            msg = rt.repo.add_chat_message(
                self.room,
                member.id,
                words,
                model_id=member.model_id,
                reply_to=reply_to,
                cost_usd=0.0 if first else (completion.cost_usd or 0.0),
                meta=meta or None,
            )
            first = first or msg
            self.on_change()
        return first


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
        if self.rt.repo.chat_room(room) is None and room.startswith(DM_PREFIX):
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
        for r in stored.values():
            if r["kind"] != "dm":
                out.append(r)
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
