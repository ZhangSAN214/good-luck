"""聊天：像微信一样和成员聊天（docs/CHAT.md）。

- 会话：生活群（``main``，全体成员）、工作群（``work``）、和每位成员的私聊（``dm:<成员>``）。
- 成员 = 每个厂商一位（昵称相同的全力 / 节电模型算一位），发言用 ``chat.tier`` 档位的模型，
  没有就用该成员最便宜的可用模型。
- 成员不主动找用户：只在用户发言后回应。群里 ``@昵称`` 指定谁回应，``@全员`` 大家都说；
  没有 @ 时随机一两位回应，后发言的人能看到前面的回应。私聊里只有那一位回应。
- 每次发言都记在 calls 表（挂在该会话的隐藏会话上，workflow = 'chat'），计入总预算和工资；
  另有聊天的月预算（``chat.budget.monthly_usd``），用完就不再调用。
- 聊天不匿名、不遮蔽；工作模式的提示词与这里无关（人设不影响工作模式）。
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from roundtable.core.budget.guard import EPS, month_window
from roundtable.core.persona import persona_of, persona_text
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
    ) -> None:
        self.rt = rt
        self.room = room
        self.rng = rng or random.Random()
        self.on_change = on_change or (lambda: None)
        self.names = names or dict
        self.typing: list[str] = []
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

    def history(self, names: dict[str, str]) -> str:
        rows = self.rt.repo.chat_messages(self.room, limit=self.rules.history_messages)
        lines = []
        for r in rows:
            if r["author"] == SYSTEM:
                continue
            who = USER_LABEL if r["author"] == USER else names.get(r["author"], r["author"])
            lines.append(f"{who}：{r['text']}")
        return "\n".join(lines)

    def post(
        self, text: str, meta: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], list[ChatMember]]:
        """存下用户的消息并选出回应的成员（回应由 replies() 生成）。

        meta 里可带图片 / 视频 / 语音清单（media）；成员只看到 text 里的文字占位。
        """
        msg = self.rt.repo.add_chat_message(self.room, USER, text, meta=meta)
        return msg, self.pick(text)

    async def replies(self, reply_to: int, speakers: list[ChatMember]) -> list[dict[str, Any]]:
        async with self._lock:
            out = []
            for member in speakers:
                said = await self._speak(member, reply_to)
                if said is None:
                    break
                if said:
                    out.append(said)
            return out

    async def _speak(self, member: ChatMember, reply_to: int) -> dict[str, Any] | None:
        """一位成员发言。返回消息；调用失败返回 {}；预算用完返回 None（后面的人也不再说）。"""
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
        history = self.history(names)
        variables: dict[str, str] = {
            "name": member.name,
            "persona": persona_text(persona_of(rt, member.id)),
            "memory": member_memory(rt, member.id, history),
            "history": history,
        }
        if self.kind != "dm":
            variables["members"] = (
                "、".join(m.name for m in self.members() if m.id != member.id) or "（没有别人）"
            )
        prompt = rt.prompts.render(role, rt.config.roundtable.prompts[role], **variables)
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
        if not text:
            # 没说出话（多半是思考把输出上限用完了）：在群里说明，不静默跳过
            why = "想得太久，没来得及写完" if completion.finish_reason == "length" else "回复是空的"
            rt.repo.add_chat_message(self.room, SYSTEM, f"{member.name} 没有回复（{why}）。")
            self.on_change()
            return {}
        msg = rt.repo.add_chat_message(
            self.room,
            member.id,
            text,
            model_id=member.model_id,
            reply_to=reply_to,
            cost_usd=completion.cost_usd or 0.0,
        )
        self.on_change()
        return msg


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
        chat = GroupChat(self.rt, room, rng=self.rng, on_change=self.on_change, names=self.names)
        self._rooms[room] = chat
        return chat

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
