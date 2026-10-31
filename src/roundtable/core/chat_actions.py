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

第二批（转发与朋友圈）：

- 卡片（``meta.card``）：帖子 / 文章 / 视频的链接卡片（标题、来源、摘要），
  来自订阅源或用户粘贴的链接；成员看到的是文字占位「[链接] 标题」。
- 生成的图片 / 动图 / 自制表情包（创作输出接好后）：``generated_item`` 存进本机聊天文件夹，
  成为普通的 ``meta.media`` 一项（``sticker: true`` 时按表情包显示），用 ``GroupChat.send_as`` /
  ``Moments.publish`` 发出。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlparse

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

    def moment_forward(self, member: str, key: str) -> bool: ...


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

    def moment_forward(self, member: str, key: str) -> bool:
        """今天上网分享过东西的成员发朋友圈时，要不要发成「一句话 + 转发卡片」。
        key 由调用方给（如事件 id），随机数由它派生，可复现。"""
        import random

        r = self.rules
        return r.enabled and random.Random(f"forward:{key}:{member}").random() < r.moment_forward


def own_stickers(rt: Any, member: str, name: str) -> dict[str, list[str]]:
    """成员自己那套审过的专属表情：{昵称: [表情名]}（套装名就是昵称）。

    没有、聊天动作关着、昵称放不进记号（太长或带分隔符）时为空。"""
    from roundtable.core.member_stickers import MemberStickers

    acts = rt.config.roundtable.chat.actions
    if not acts.enabled or not STICKER_RE.fullmatch(f"[表情:{name}·x]") or name in acts.stickers:
        return {}
    names = [s["name"] for s in MemberStickers(rt, rt.sticker_dir).approved(member)]
    names = [n for n in names if STICKER_RE.fullmatch(f"[表情:{name}·{n}]")]
    return {name: names} if names else {}


def member_stickers(rt: Any, member: str, name: str) -> dict[str, list[str]]:
    """这位成员能发的表情包：自己的专属那套在前，再加通用的几套和她从别人那收来的；
    聊天动作关着时为空。"""
    from roundtable.core.member.stash import Stash  # 收藏读成员档案，用到再导入

    acts = rt.config.roundtable.chat.actions
    if not acts.enabled:
        return {}
    return own_stickers(rt, member, name) | acts.stickers | Stash(rt).pack(member)


def sticker_menu(rt: Any, member: str, name: str) -> str:
    """提示词里给这位成员的表情包清单：有专属表情时分成「你的专属」和「通用」两段。"""
    from roundtable.core.member.stash import Stash

    own = own_stickers(rt, member, name)
    common = sticker_list(rt.config.roundtable.chat.actions.stickers)
    menu = f"你的专属：{sticker_list(own)}；通用：{common}" if own else common
    stash = Stash(rt).menu(member)
    return f"{menu}；{stash}" if stash else menu


def sticker_list(stickers: dict[str, list[str]]) -> str:
    """提示词里的表情包清单：「[表情:团子·开心]、……」。"""
    return "、".join(f"[表情:{pack}·{name}]" for pack, names in stickers.items() for name in names)


def clean_stickers(text: str, stickers: dict[str, list[str]]) -> str:
    """只留认识的表情包记号（统一成半角冒号）；不认识的删掉。"""

    def keep(m: re.Match[str]) -> str:
        pack, name = m.group(1), m.group(2)
        return f"[表情:{pack}·{name}]" if name in stickers.get(pack, ()) else ""

    return STICKER_RE.sub(keep, text)


def swap_repeated_stickers(
    text: str, used: set[str], stickers: dict[str, list[str]], rng: Any
) -> str:
    """同一轮里别人已经发过的表情包，换成同一套里还没人发过的（都发过了就留着）；
    发出的表情包记进 used。"""

    def swap(m: re.Match[str]) -> str:
        pack, name = m.group(1), m.group(2)
        token = f"[表情:{pack}·{name}]"
        if token in used:
            free = [n for n in stickers.get(pack, ()) if f"[表情:{pack}·{n}]" not in used]
            if free:
                token = f"[表情:{pack}·{rng.choice(free)}]"
        used.add(token)
        return token

    return STICKER_RE.sub(swap, text)


def drop_own_repeats(text: str, mine: set[str]) -> str:
    """她最近刚发过的表情包又跟在一句话后面：去掉（10-31 第 3 次试跑：连着 3 条同一个「疑惑」）。
    只发了一个表情包的不动（交给 swap_repeated_stickers 换一个）。"""
    if not mine or not STICKER_RE.search(text):
        return text
    bare = STICKER_RE.sub("", text).strip()
    if not bare:
        return text

    def drop(m: re.Match[str]) -> str:
        return "" if f"[表情:{m.group(1)}·{m.group(2)}]" in mine else m.group(0)

    return re.sub(r"[ \t]+$", "", STICKER_RE.sub(drop, text), flags=re.M)


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


# --- 卡片（转发帖子 / 文章 / 视频） ---------------------------------------------------

CARD_KINDS = ("article", "post", "video")
CARD_LABELS = {"article": "链接", "post": "帖子", "video": "视频"}
URL_RE = re.compile(r"^https?://[^\s<>\"']{3,1000}$")


def card_kind(
    url: str, feed_kind: str = "news", video_hosts: list[str] | tuple[str, ...] = ()
) -> str:
    """订阅源是视频源、或网址在视频站上 → video；论坛 → post；其余 article。"""
    host = (urlparse(url).hostname or "").lower()
    if feed_kind == "video" or any(host == h or host.endswith("." + h) for h in video_hosts):
        return "video"
    return "post" if feed_kind == "forum" else "article"


def card_from_item(item: dict[str, Any], feed_kind: str, video_hosts: list[str]) -> dict[str, Any]:
    """订阅源 / 搜索结果的一条 → 卡片。"""
    return {
        "kind": card_kind(item["url"], feed_kind, video_hosts),
        "title": str(item["title"])[:120],
        "url": item["url"],
        "source": str(item.get("feed") or urlparse(item["url"]).hostname or "")[:40],
        "summary": snippet(item.get("summary") or ""),
        "published": item.get("published"),
        "unverified": bool(item.get("unverified")),
    }


def card_from_url(
    url: str, video_hosts: list[str], known: dict[str, Any] | None = None
) -> dict[str, Any]:
    """用户粘贴的链接 → 卡片。不联网：订阅源里有这条就用它的标题，否则用网址本身。"""
    if known:
        return card_from_item(known, "news" if not known.get("unverified") else "blog", video_hosts)
    host = urlparse(url).hostname or ""
    return {
        "kind": card_kind(url, "news", video_hosts),
        "title": url[:120],
        "url": url,
        "source": host[:40],
        "summary": "",
        "published": None,
        "unverified": True,
    }


def is_bare_url(text: str) -> bool:
    return bool(URL_RE.match(text.strip()))


def card_text(card: dict[str, Any]) -> str:
    """给成员看的文字占位。"""
    return f"[{CARD_LABELS.get(card.get('kind', ''), '链接')}] {card.get('title', '')}"


def clean_card(card: Any) -> dict[str, Any] | None:
    """检查卡片字段（转发、接入口传进来的）：只接受 http(s) 网址。"""
    if not isinstance(card, dict) or not URL_RE.match(str(card.get("url") or "")):
        return None
    kind = card.get("kind") if card.get("kind") in CARD_KINDS else "article"
    return {
        "kind": kind,
        "title": str(card.get("title") or card["url"])[:120],
        "url": str(card["url"]),
        "source": str(card.get("source") or "")[:40],
        "summary": snippet(str(card.get("summary") or "")),
        "published": card.get("published") if isinstance(card.get("published"), str) else None,
        "unverified": bool(card.get("unverified", True)),
    }


# --- 生成的图片 / 动图 / 自制表情包（接入口） ------------------------------------------


def generated_item(store: Any, data: bytes, mime: str, *, sticker: bool = False) -> dict[str, Any]:
    """把生成的图片 / 动图存进本机聊天文件夹，返回一项 ``meta.media``。

    创作输出（画图、做动图）接好后调用它，再用 ``GroupChat.send_as`` 或 ``Moments.publish`` 发出。
    sticker 为真时网页按表情包显示（大图、没有气泡）。格式和大小限制与用户发的图片相同。
    """
    saved = store.save(data, mime)
    if saved.kind != "image":
        raise ValueError("只能是图片或动图")
    item: dict[str, Any] = {"id": saved.id, "kind": "image", "generated": True}
    if sticker:
        item["sticker"] = True
    return item
