"""聊天里能发什么：表情包、颜文字、引用、拆条、不接话、拍一拍、撤回（docs/CHAT.md「微信式动作」）。

本模块只管「能发什么」：动作的格式、从模型输出里解析、存进消息的附加信息（``chat_messages.meta``，
不加迁移）。什么时候发、发不发、节奏和延迟归成员系统：它实现 ``ActionPolicy`` 后赋给
``ChatHub.policy`` 即可接管；没接管时用 ``DefaultPolicy``（按 ``chat.actions`` 的配置，
所有成员相同）。

模型输出里的记号（写法在聊天提示词里说明，这里只解析）：

- ``[表情:套装·名字]``：表情包，只认 ``chat.actions.stickers`` 里有的，别的删掉；
- ``[引用#12]``：放在一段开头，引用聊天记录里编号为 12 的消息（只认最近记录里有的编号）；
- 空行：把一次回复拆成几条气泡，最多 ``max_segments`` 条，多出来的并进最后一条；
- ``[不回]``：这次不接话（只在允许时生效）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

NO_REPLY = "[不回]"
STICKER_RE = re.compile(r"\[表情[:：]([^\]·]{1,6})·([^\]]{1,6})\]")
QUOTE_HEAD_RE = re.compile(r"^\s*\[引用\s*#?\s*(\d+)\]\s*")
QUOTE_ANY_RE = re.compile(r"\[引用\s*#?\s*\d*\]")
SNIPPET_CHARS = 60


@dataclass(frozen=True)
class Segment:
    text: str
    quote: int | None = None


@dataclass(frozen=True)
class Parsed:
    segments: list[Segment] = field(default_factory=list)
    no_reply: bool = False


class ActionPolicy(Protocol):
    """成员系统接管「发不发、什么节奏」的口子。member 是成员 id，room_kind 是 group / dm / work。"""

    def max_segments(self, member: str, room_kind: str) -> int: ...

    def segment_gap(self, member: str, text: str) -> float: ...

    def allow_no_reply(self, member: str, room_kind: str, named: bool) -> bool: ...

    def reply_to_pat(self, member: str, room_kind: str) -> bool: ...

    def drop_stale(self, room_kind: str) -> bool: ...

    def pat_suffix(self, member: str) -> str: ...

    def voice(self, member: str, room_kind: str, seed: int = 0) -> str: ...


class DefaultPolicy:
    """没接管时的默认值：按配置，所有成员相同（中立）。"""

    def __init__(self, rt: Any) -> None:
        self.rt = rt

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.chat.actions

    def max_segments(self, member: str, room_kind: str) -> int:
        if not self.rules.enabled or room_kind == "work":
            return 1
        return self.rules.max_segments

    def segment_gap(self, member: str, text: str) -> float:
        return self.rules.segment_gap_seconds

    def allow_no_reply(self, member: str, room_kind: str, named: bool) -> bool:
        return (
            self.rules.enabled
            and self.rules.no_reply_in_group
            and room_kind == "group"
            and not named
        )

    def reply_to_pat(self, member: str, room_kind: str) -> bool:
        return self.rules.enabled and self.rules.pat_reply

    def drop_stale(self, room_kind: str) -> bool:
        return self.rules.drop_stale

    def pat_suffix(self, member: str) -> str:
        return ""

    def voice(self, member: str, room_kind: str, seed: int = 0) -> str:
        """提示词里这位成员的「说话习惯」（成员系统接管时给，seed 用来轮换示范句）；默认没有。"""
        return ""


def sticker_list(stickers: dict[str, list[str]]) -> str:
    """提示词里的表情包清单：「[表情:团子·开心]、……」。"""
    return "、".join(f"[表情:{pack}·{name}]" for pack, names in stickers.items() for name in names)


def clean_stickers(text: str, stickers: dict[str, list[str]]) -> str:
    """只留认识的表情包记号（统一成半角冒号）；不认识的删掉。"""

    def keep(m: re.Match[str]) -> str:
        pack, name = m.group(1), m.group(2)
        return f"[表情:{pack}·{name}]" if name in stickers.get(pack, ()) else ""

    return STICKER_RE.sub(keep, text)


def parse(
    text: str,
    *,
    stickers: dict[str, list[str]],
    max_segments: int,
    quotable: set[int],
    allow_no_reply: bool = False,
) -> Parsed:
    """把一次回复解析成几条气泡。全部为空时 segments 为空（no_reply 表示是有意不接话）。"""
    raw = text.strip()
    if NO_REPLY in raw:
        if allow_no_reply:
            return Parsed(no_reply=True)
        raw = raw.replace(NO_REPLY, "").strip()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", raw) if b.strip()]
    if max_segments >= 1 and len(blocks) > max_segments:
        blocks = [*blocks[: max_segments - 1], "\n".join(blocks[max_segments - 1 :])]
    out = []
    for block in blocks:
        quote = None
        m = QUOTE_HEAD_RE.match(block)
        if m:
            block = block[m.end() :]
            quote = int(m.group(1)) if int(m.group(1)) in quotable else None
        block = clean_stickers(QUOTE_ANY_RE.sub("", block), stickers).strip()
        if block:
            out.append(Segment(block, quote))
    return Parsed(out)


def snippet(text: str) -> str:
    t = " ".join(text.split())
    return t[:SNIPPET_CHARS] + ("…" if len(t) > SNIPPET_CHARS else "")


def quote_meta(row: dict[str, Any]) -> dict[str, Any]:
    """引用框里存的东西：被引用消息的 id、作者和开头一段文字。"""
    return {"id": row["id"], "author": row["author"], "text": snippet(row["text"])}


def is_recalled(row: dict[str, Any]) -> bool:
    return bool((row.get("meta") or {}).get("recalled"))
