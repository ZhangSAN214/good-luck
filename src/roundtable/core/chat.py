"""群聊：像微信群一样和成员聊天（docs/PLAN.md 阶段 32 的最小版本）。

- 成员 = 每个厂商一位（昵称相同的全力 / 节电模型算一位），发言用 ``chat.tier`` 档位的模型，
  没有就用该成员最便宜的可用模型。
- 成员不主动发消息：只在用户发言后回应。``@昵称`` 指定谁回应，``@全员`` 大家都说；
  没有 @ 时随机一两位回应。后发言的人能看到前面的回应。
- 每次发言都记在 calls 表（挂在群聊的隐藏会话上），计入预算和工资；预算用完就不再调用。
- 群聊不匿名、不遮蔽；工作模式的提示词与这里无关（人设不影响工作模式）。
"""

from __future__ import annotations

import asyncio
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from roundtable.core.providers import AllChannelsFailed, NoChannelAvailable, ProviderError

ROOM = "main"
USER = "user"
SYSTEM = "system"
EVERYONE = "全员"
USER_LABEL = "用户"


@dataclass(frozen=True)
class ChatMember:
    id: str  # 厂商
    name: str  # 昵称
    model_id: str
    available: bool


def _price(m: Any) -> tuple[float, float]:
    return (m.price.output, m.price.input)


def chat_members(config: Any, available: set[str]) -> list[ChatMember]:
    """每个厂商一位；按配置顺序。"""
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
                name=config.personas.nicknames.get(vendor, vendor),
                model_id=pick.id,
                available=pick.id in available,
            )
        )
    return members


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


class GroupChat:
    """一个群（默认只有一个）。replies() 在后台依次生成回应；typing 是正在输入的成员名。"""

    def __init__(
        self,
        rt: Any,
        room: str = ROOM,
        rng: random.Random | None = None,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.rt = rt
        self.room = room
        self.rng = rng or random.Random()
        self.on_change = on_change or (lambda: None)
        self.typing: list[str] = []
        self._lock = asyncio.Lock()

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat

    def members(self) -> list[ChatMember]:
        available = {m.id for m in self.rt.router.available_models()}
        return chat_members(self.rt.config, available)

    def pick(self, text: str) -> list[ChatMember]:
        members = self.members()
        online = [m for m in members if m.available]
        named = [m for m in mentioned(text, members) if m.available]
        if f"@{EVERYONE}" in text:
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

    def post(self, text: str) -> tuple[dict[str, Any], list[ChatMember]]:
        """存下用户的消息并选出回应的成员（回应由 replies() 生成）。"""
        msg = self.rt.repo.add_chat_message(self.room, USER, text)
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
        everyone = self.members()
        names = {m.id: m.name for m in everyone}
        others = "、".join(m.name for m in everyone if m.id != member.id) or "（没有别人）"
        prompt = rt.prompts.render(
            "chat",
            rt.config.roundtable.prompts["chat"],
            name=member.name,
            members=others,
            history=self.history(names),
        )
        session = rt.repo.chat_session(self.room)
        self.typing.append(member.name)
        self.on_change()
        try:
            completion = await rt.router.complete(
                member.model_id, prompt.messages, {"max_tokens": rules.max_tokens}
            )
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
