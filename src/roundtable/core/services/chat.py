"""群聊与私聊：群聊、聊天里的图片视频语音、聊天交办、成员设定与记忆、外部表情包、记忆与联想、自我档案、关系、关于你、成长。

从 service.py 拆出（代码精简）。
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from typing import Any
from urllib.parse import quote

from roundtable.core import chat_handoff as handoff
from roundtable.core.chat import ROOM, WORK, chat_budget
from roundtable.core.chat_actions import card_from_url, is_bare_url, own_stickers
from roundtable.core.chat_actions import card_text as chat_card_text
from roundtable.core.chat_media import ChatMediaStore, MediaError, clean_items, placeholder
from roundtable.core.chat_work import post_handoff
from roundtable.core.member import ProfileError
from roundtable.core.member.about import AboutError, AboutMissing
from roundtable.core.member.bus import Event as MemberEvent
from roundtable.core.member.card import (
    card_patch,
    match_members,
    parse_cards,
    source_title,
    without_locked,
)
from roundtable.core.member.growth import week_id
from roundtable.core.member.growth.book import SOURCES as BOOK_SOURCES
from roundtable.core.member.growth.plastic import plasticity
from roundtable.core.member.growth.report import lines as report_lines
from roundtable.core.member.memory import DiaryTool
from roundtable.core.member.memory.store import query_terms
from roundtable.core.member.relation import view_live
from roundtable.core.member_stickers import StickerBudgetExceeded, StickerError, StickerUnavailable
from roundtable.core.member_voice import MAX_CHARS as VOICE_MAX_CHARS
from roundtable.core.member_voice import (
    SAMPLE_TEXT,
    VoiceBudgetExceeded,
    VoiceClip,
    VoiceUnavailable,
)
from roundtable.core.memory import LAYERS, clean_memory, prepare_items
from roundtable.core.moments import SEEN_KEY as MOMENTS_SEEN
from roundtable.core.perception import SenseItem
from roundtable.core.persona import card_text, is_empty, persona_of, set_persona
from roundtable.core.services.common import (
    FORWARD_KEYS,
    HANDOFF_FILES,
    MAX_CHAT_CHARS,
    ServiceError,
)
from roundtable.core.sticker_packs import PackError
from roundtable.core.storage import NotFound
from roundtable.core.yourpc import game_of


class ChatService:
    """群聊与私聊：群聊、聊天里的图片视频语音、聊天交办、成员设定与记忆、外部表情包、记忆与联想、自我档案、关系、关于你、成长。"""

    # --- 群聊 ----------------------------------------------------------------------

    def _chat_room(self, room: str) -> Any:
        try:
            return self.chats.room(room or ROOM)
        except KeyError:
            raise ServiceError(f"没有这个聊天：{room}", 404) from None

    def chat_rooms(self) -> dict[str, Any]:
        """会话列表（群、私聊，带未读数和最后一条消息）与聊天本月预算。"""
        budget = chat_budget(self.rt)
        return {
            "rooms": self.chats.rooms(),
            "members": [asdict(m) for m in self.chats.everyone()],
            "typing": self.chats.typing(),
            "queued": self.chats.queued(),
            "budget": {"spent_usd": budget.spent_usd, "limit_usd": budget.limit_usd},
            "moments_unseen": self.rt.repo.unseen_moments(
                int(self.rt.repo.setting(MOMENTS_SEEN) or 0)
            ),
            "life": {
                "enabled": self.rt.config.roundtable.chat.life.enabled,
                "spent_today_usd": self.life.spent_today(),
                "daily_usd": self.rt.config.roundtable.chat.life.daily_usd,
                "messages_today": self.life.guard.messages_today(),
                "daily_messages": self.rt.config.roundtable.chat.life.guard.daily_messages,
                "halted": self.life.guard.halted(),
            },
        }

    def chat_view(
        self, after: int = 0, query: str | None = None, room: str = ROOM
    ) -> dict[str, Any]:
        """会话成员、消息（id 大于 after 的；有 query 时为搜索结果）与正在输入的成员。

        不是搜索时把会话标为已读。
        """
        chat = self._chat_room(room)
        everyone = chat.everyone()
        names = {m.id: m.name for m in everyone} | {"user": "我", "system": "系统"}
        rows = self.rt.repo.chat_messages(chat.room, after=after, query=query or None)
        if rows and not query:
            self.rt.repo.mark_chat_read(chat.room, rows[-1]["id"])
        return {
            "room": {"id": chat.room, "kind": chat.kind, "title": chat.title},
            "members": [asdict(m) for m in chat.members()],
            "messages": [
                {
                    "id": r["id"],
                    "author": r["author"],
                    "name": names.get(r["author"], r["author"]),
                    "text": r["text"],
                    "reply_to": r["reply_to"],
                    "cost_usd": r["cost_usd"],
                    "meta": r["meta"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
            "typing": list(chat.typing),
            "queued": list(chat.queued),
            "recall_seconds": self.rt.config.roundtable.chat.actions.recall_seconds,
        }

    # --- 聊天 / 朋友圈里的图片、视频、语音（只存本机） ------------------------------

    @property
    def chat_media(self) -> ChatMediaStore:
        return ChatMediaStore(self.rt.chat_media_dir)

    def chat_media_save(self, data: bytes, mime: str) -> dict[str, Any]:
        """存一个用户发的图片 / 视频 / 语音，返回 id 和种类；发消息时把 id 带上。"""
        try:
            saved = self.chat_media.save(data, mime)
        except MediaError as exc:
            raise ServiceError(str(exc), 413 if "太大" in str(exc) else 400) from None
        return {"id": saved.id, "kind": saved.kind, "mime": saved.mime, "size": saved.size}

    def chat_media_file(self, name: str) -> tuple[bytes, str]:
        found = self.chat_media.load(name)
        if found is None:
            raise ServiceError("文件不存在", 404)
        return found

    def _media_meta(self, text: str, media: list[dict] | None) -> tuple[str, dict | None]:
        """检查附件；没有文字时用「[图片]」之类的占位当作给成员看的内容。"""
        try:
            items = clean_items(self.chat_media, media)
        except MediaError as exc:
            raise ServiceError(str(exc)) from None
        if not items:
            return text, None
        meta: dict[str, Any] = {"media": items}
        tag = placeholder(items)
        if not text:
            meta["auto_text"] = True
            return tag, meta
        return f"{text}\n{tag}", meta | {"caption": text}

    def _file_meta(
        self, text: str, meta: dict | None, files: Sequence[str]
    ) -> tuple[str, dict | None]:
        """聊天里发的文件（先 upload 得到附件 id）：meta.files 记名字和大小，
        文字后面补「[文件 名字]」。"""
        if not files:
            return text, meta
        items = []
        for fid in list(dict.fromkeys(files))[:HANDOFF_FILES]:
            try:
                row = self.rt.repo.attachment(fid)
            except NotFound:
                raise ServiceError("文件不存在，请重新上传") from None
            items.append({"id": fid, "name": row["name"], "kind": row["kind"], "size": row["size"]})
        tags = " ".join(f"[文件 {it['name']}]" for it in items)
        out = dict(meta or {})
        out["files"] = items
        if not text:
            out.setdefault("auto_text", True)
            return tags, out
        if "caption" not in out:
            out["caption"] = text
        return f"{text}\n{tags}", out

    async def chat_post(
        self,
        text: str,
        room: str = ROOM,
        media: list[dict] | None = None,
        quote: int | None = None,
        *,
        files: Sequence[str] = (),
    ) -> dict[str, Any]:
        """用户发一条消息（可带图片 / 视频 / 语音、文件，可引用一条消息）；被选中的成员在后台
        依次回应。文件先用 upload() 上传；成员只看到文件名，要读里面的内容时交给办公室
        （带着文件一起）。"""
        chat = self._chat_room(room)
        if chat.kind == "pair":
            raise ServiceError("这是成员之间的私聊，你只能看，不能发言")
        text = (text or "").strip()
        if len(text) > MAX_CHAT_CHARS:
            raise ServiceError(f"消息太长（最多 {MAX_CHAT_CHARS} 字）")
        text, meta = self._media_meta(text, media)
        text, meta = self._file_meta(text, meta, files)
        if not text:
            raise ServiceError("消息不能为空")
        if meta is None and is_bare_url(text):  # 只发了一个链接：显示成卡片
            hosts = self.rt.config.roundtable.chat.actions.video_hosts
            card = card_from_url(text, hosts, self.rt.repo.web_item_by_url(text))
            text, meta = chat_card_text(card), {"card": card}
        try:
            msg, speakers = chat.post(text, meta, quote)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        self.rt.repo.mark_chat_read(chat.room, msg["id"])
        with contextlib.suppress(Exception):  # 状态出错不影响聊天
            self.engine.observe(self.rt.budget.now())  # 被 @、被批评的反射在回应前算进状态
        mode = self.perceiver.mode_for(text) if meta else None
        if mode:
            self._background(self._look_and_reply(chat, msg["id"], speakers, mode, chat.round))
        else:
            self._chat_replies(chat, msg["id"], speakers)
        return {"id": msg["id"], "speakers": [m.name for m in speakers], "look": mode}

    async def _look_and_reply(
        self, chat: Any, message_id: int, speakers: list[Any], mode: str, round_no: int
    ) -> None:
        """消息带图片 / 视频 / 语音时，先由感官整理成感知卡（成员读卡），再依次回应。"""
        with contextlib.suppress(Exception):  # 没看成也照常回应，成员只看到占位
            await self._perceive_message(chat.room, message_id, mode)
        if speakers:  # 用户在感官看的时候又发了消息或撤回：按策略作废这一轮
            await chat.replies(message_id, speakers, round_no)

    def _sense_items(self, meta: dict[str, Any]) -> list[SenseItem]:
        items = []
        for it in meta.get("media") or []:
            found = self.chat_media.load(it["id"])
            if found is not None:
                items.append(SenseItem(it["id"], it["kind"], found[0], found[1]))
        return items

    async def _perceive_message(self, room: str, message_id: int, mode: str) -> list[dict]:
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["room"] != room:
            return []
        meta = dict(row["meta"] or {})
        items = self._sense_items(meta)
        if not items:
            return []
        chat = self._chat_room(room)
        chat.typing.append("感官")
        chat.on_change()
        try:
            cards = await self.perceiver.perceive(room, items, mode)
        finally:
            chat.typing.remove("感官")
            chat.on_change()
        if cards:
            meta["perception"] = [
                {"id": c.id, "kind": c.kind, "mode": c.mode, "text": c.text} for c in cards
            ]
            self.rt.repo.set_chat_message_meta(message_id, meta)
            chat.on_change()
        return meta.get("perception", [])

    async def chat_look(self, room: str, message_id: int) -> dict[str, Any]:
        """「细看」：把一条带图片 / 视频 / 语音的消息按精细模式重新整理，再请一位成员看过后回应。

        转写稿和缓存复用；已经是精细的直接用缓存（不花钱）。
        """
        chat = self._chat_room(room)
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["room"] != chat.room or row["author"] != "user":
            raise ServiceError("消息不存在", 404)
        if not (row["meta"] or {}).get("media"):
            raise ServiceError("这条消息没有图片、视频或语音")
        if not self.rt.config.roundtable.perception.enabled:
            raise ServiceError("感官已关闭（perception.enabled）")
        cards = await self._perceive_message(chat.room, message_id, "fine")
        if not any(c["mode"] == "fine" for c in cards):
            raise ServiceError("这次没能细看（没有可用的看图模型，或本月感官额度已用完）")
        replied = [
            r["author"]
            for r in self.rt.repo.chat_messages(chat.room, after=message_id, limit=50)
            if r["reply_to"] == message_id and r["author"] not in ("user", "system")
        ]
        online = [m for m in chat.members() if m.available]
        speakers = [m for m in online if m.id in replied][:1] or chat.pick(row["text"])[:1]
        self._chat_replies(chat, message_id, speakers)
        return {"id": message_id, "speakers": [m.name for m in speakers]}

    def _chat_replies(self, chat: Any, reply_to: int, speakers: list[Any]) -> None:
        if not speakers:
            return
        if chat.kind == "dm":  # 回话节拍：等你说完、她在忙时晚一会儿
            coro = self._paced_replies(chat, reply_to, speakers, chat.round)
        else:
            coro = chat.replies(reply_to, speakers, chat.round)
        task = asyncio.get_running_loop().create_task(coro)
        self._chat_tasks.add(task)
        task.add_done_callback(self._chat_tasks.discard)

    async def _paced_replies(
        self, chat: Any, reply_to: int, speakers: list[Any], round_no: int
    ) -> list[dict[str, Any]]:
        """私聊：你连着发时先等一小会儿看你说完没（又发了就交给新的那一轮）；她在忙时再晚一会儿。"""
        from roundtable.core.member.pace import Pace

        pace = Pace(self.rt)
        wait = pace.debounce(chat.room, self.rt.budget.now())
        if wait:
            await chat.sleep(wait)
            if chat.round != round_no:
                return []
        row = self.rt.repo.chat_message(reply_to)
        text = str(row["text"] or "") if row is not None else ""
        delay = max(
            (pace.busy_delay(m.id, chat.room, text, self.rt.budget.now()) for m in speakers),
            default=0.0,
        )
        if delay:
            await chat.sleep(delay)
            if chat.round != round_no:
                return []
        return await chat.replies(reply_to, speakers, round_no)

    async def chat_pat(self, member: str, room: str = ROOM) -> dict[str, Any]:
        """拍一拍会话里的一位成员：发 chat.pat 事件给成员系统，按策略（默认回一句）让 TA 回应。"""
        chat = self._chat_room(room)
        if chat.kind == "pair":
            raise ServiceError("这是成员之间的私聊，你只能看")
        try:
            msg = chat.pat(member)
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        self.rt.repo.mark_chat_read(chat.room, msg["id"])
        now = self.rt.budget.now()
        self.engine.bus.publish(
            MemberEvent(
                "chat.pat", now, member, {"room": chat.room, "id": msg["id"], "actor": "user"}
            )
        )
        target = next((m for m in chat.members() if m.id == member and m.available), None)
        speakers = []
        if target is not None and self.chats.policy.reply_to_pat(member, chat.kind):
            speakers = [target]
            chat.round += 1  # 拍一拍也算新的一轮：上一轮还没发出的按策略作废
        self._chat_replies(chat, msg["id"], speakers)
        return {"id": msg["id"], "text": msg["text"], "speakers": [m.name for m in speakers]}

    async def chat_forward(self, message_id: int, room: str) -> dict[str, Any]:
        """把一条消息（文字、图片、表情包、卡片）原样转发到另一个会话，以你的身份发出；
        那边的成员照常回应。"""
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["author"] == "system" or (row["meta"] or {}).get("recalled"):
            raise ServiceError("这条消息不能转发")
        src = self._chat_room(row["room"])
        chat = self._chat_room(room)
        if chat.kind == "pair":
            raise ServiceError("这是成员之间的私聊，你只能看")
        meta = {k: v for k, v in (row["meta"] or {}).items() if k in FORWARD_KEYS}
        names = {m.id: m.name for m in src.everyone()} | {"user": "我"}
        meta["forwarded"] = {"from": names.get(row["author"], row["author"]), "room": src.title}
        msg, speakers = chat.post(row["text"], meta)
        self.rt.repo.mark_chat_read(chat.room, msg["id"])
        self._chat_replies(chat, msg["id"], speakers)
        return {"id": msg["id"], "speakers": [m.name for m in speakers]}

    def chat_recall(self, message_id: int, room: str = ROOM) -> dict[str, Any]:
        """撤回你自己发的一条消息（发出后几分钟内）。"""
        chat = self._chat_room(room)
        try:
            row = chat.recall(message_id, self.rt.budget.now())
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        return {"id": row["id"], "recalled": True}

    def chat_handoff(self, text: str) -> dict[str, Any]:
        """在工作群里交办：按办公室模式（默认档位）开一件事，工作群里记下这条交办。"""
        text = (text or "").strip()
        if not text:
            raise ServiceError("要交办的事不能为空")
        sid = self.create(text, workflow="office")
        msg = post_handoff(self.rt, sid, text)
        self.rt.repo.mark_chat_read(WORK, msg["id"])
        return {"id": msg["id"], "session_id": sid}

    # --- 聊天里的活交给办公室（docs/CHAT.md G14） ------------------------------------

    def chat_handoff_start(self, room: str, message_id: int) -> dict[str, Any]:
        """你点了交办卡片上的「交给办公室」：按办公室模式开一件事（题目只有要做的事和你的原话，
        不带人设和聊天记忆），你在这段聊天里刚发的文件一起带上。做完以那位成员的名义发回来。"""
        chat = self._chat_room(room)
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["room"] != chat.room:
            raise ServiceError("消息不存在", 404)
        card = (row.get("meta") or {}).get("handoff") or {}
        if not card:
            raise ServiceError("这条消息没有要交给办公室的事")
        if card.get("state") != handoff.PENDING:
            raise ServiceError("这件事已经处理过了")
        request = self._handoff_request(chat.room, row)
        files = self._handoff_files(chat.room, row)
        sid = self.create(
            handoff.question(card["task"], request), workflow="office", attachments=files
        )
        handoff.remember(self.rt, sid, room=chat.room, member=row["author"], message_id=row["id"])
        handoff.mark(self.rt, row["id"], state=handoff.RUNNING, session_id=sid)
        chat.on_change()
        return {"session_id": sid}

    def chat_handoff_dismiss(self, room: str, message_id: int) -> dict[str, Any]:
        """「先不用」：卡片收起，不花钱。"""
        chat = self._chat_room(room)
        row = self.rt.repo.chat_message(message_id)
        if row is None or row["room"] != chat.room:
            raise ServiceError("消息不存在", 404)
        card = (row.get("meta") or {}).get("handoff") or {}
        if card.get("state") != handoff.PENDING:
            raise ServiceError("这件事已经处理过了")
        handoff.mark(self.rt, row["id"], state=handoff.DISMISSED)
        chat.on_change()
        return {"ok": True}

    def _handoff_request(self, room: str, row: dict[str, Any]) -> str:
        """成员交办时回的是你哪句话（她回应的那条，或者她之前你说的最后一句）。"""
        if row.get("reply_to"):
            asked = self.rt.repo.chat_message(row["reply_to"])
            if asked is not None and asked["author"] == "user":
                return str(asked["text"] or "")
        before = [
            r
            for r in self.rt.repo.chat_messages(room, limit=30)
            if r["id"] < row["id"] and r["author"] == "user"
        ]
        return str(before[-1]["text"] or "") if before else ""

    def _handoff_files(self, room: str, row: dict[str, Any]) -> list[str]:
        """你在这段聊天里（交办前最近几条）发的文件：作为附件交给办公室。"""
        out: list[str] = []
        for r in self.rt.repo.chat_messages(room, limit=30):
            if r["id"] >= row["id"] or r["author"] != "user":
                continue
            for f in (r.get("meta") or {}).get("files") or []:
                if isinstance(f, dict) and f.get("id") and f["id"] not in out:
                    out.append(str(f["id"]))
        return out[-HANDOFF_FILES:]

    # --- 成员设定与记忆（只用于聊天） ------------------------------------------------

    def _chat_member(self, member: str) -> Any:
        found = next((m for m in self.chats.everyone() if m.id == member), None)
        if found is None:
            raise ServiceError(f"没有这位成员：{member}", 404)
        return found

    def member_voices(self) -> dict[str, Any]:
        """每位成员的固定音色：现在会用哪个音色（不含模型名，匿名无关：聊天不匿名）。"""
        out = []
        for m in self.chats.everyone():
            pick = self.voices.model_for(m.id, SAMPLE_TEXT)
            out.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "available": pick is not None,
                    "voice": pick[1] if pick else None,
                }
            )
        return {"members": out, "max_chars": VOICE_MAX_CHARS}

    async def member_voice(self, member: str, text: str | None = None) -> VoiceClip:
        """用成员的固定音色念一段文字（省略 text 时念一句试听）。花费计入创作输出的每月上限。"""
        found = self._chat_member(member)
        try:
            return await self.voices.speak(member, text or SAMPLE_TEXT.format(name=found.name))
        except ValueError as exc:
            raise ServiceError(str(exc)) from None
        except VoiceBudgetExceeded as exc:
            raise ServiceError(str(exc), 402) from None
        except VoiceUnavailable as exc:
            raise ServiceError(str(exc), 503) from None

    def member_stickers(self) -> dict[str, Any]:
        """每位有形象卡的成员的专属表情：画了没有、待审还是可以用（不含模型名）。"""
        names = {m.id: m.name for m in self.chats.everyone()}
        rules = self.rt.config.roundtable.media.stickers
        members = [
            {
                "id": member,
                "name": names.get(member, member),
                "stickers": self.stickers.listing(member),
            }
            for member in self.rt.config.personas.sticker_cards
        ]
        return {
            "members": members,
            "stash": self._stash_listing(names),
            "available": bool(self.stickers.sticker_models()),
            "review": rules.review,
            "month_spent_usd": round(self.stickers.month_spent(), 4),
            "monthly_usd": rules.monthly_usd,
        }

    def _stash_listing(self, names: dict[str, str]) -> list[dict[str, Any]]:
        """成员从你那收来的表情包（只存本机）：谁收了哪几张、用过几次。"""
        from roundtable.core.member.stash import Stash

        return [
            {"id": member, "name": names.get(member, member), "items": items}
            for member, items in Stash(self.rt).listing().items()
            if items
        ]

    def stash_delete(self, member: str, n: int) -> dict[str, Any]:
        """删掉成员收藏的一张表情包（图片本身还在聊天记录里）。"""
        from roundtable.core.member.stash import Stash

        if not Stash(self.rt).delete(member, n):
            raise ServiceError("没有这张收藏", 404)
        return {"deleted": n}

    def stash_clear(self, member: str | None = None) -> dict[str, Any]:
        """一键清空成员收藏的表情包（member 为空时清空所有人的）。"""
        from roundtable.core.member.stash import Stash

        return {"cleared": Stash(self.rt).clear(member)}

    async def draw_sticker(self, member: str, name: str) -> dict[str, Any]:
        """画（或重画）成员的一个专属表情；花费计入表情包和创作输出的每月上限。"""
        try:
            return await self.stickers.draw(member, name)
        except StickerError as exc:
            raise ServiceError(str(exc), 404) from None
        except StickerBudgetExceeded as exc:
            raise ServiceError(str(exc), 402) from None
        except StickerUnavailable as exc:
            raise ServiceError(str(exc), 503) from None

    def review_sticker(self, member: str, name: str, status: str) -> dict[str, Any]:
        try:
            return self.stickers.review(member, name, status)
        except StickerError as exc:
            raise ServiceError(str(exc)) from None

    def sticker_file(self, member: str, name: str) -> bytes:
        data = self.stickers.file(member, name)
        if data is None:
            raise ServiceError("这个表情还没画", 404)
        return data

    def emoji_status(self) -> dict[str, Any]:
        """网页里的 emoji 显示成哪套图：{enabled, style}。"""
        return self.emoji.status()

    def emoji_image(self, code: str) -> bytes:
        """一个 emoji 的图（用到才下载，存本机）；没有时 404，网页用系统字体。"""
        data = self.emoji.image(code)
        if data is None:
            raise ServiceError("没有这个表情图", 404)
        return data

    def chat_sticker_packs(self) -> dict[str, Any]:
        """聊天里能显示的成员专属表情（审过的）：套装名是昵称，图从 /api/stickers/… 取。"""
        packs = []
        for m in self.chats.everyone():
            own = own_stickers(self.rt, m.id, m.name).get(m.name, [])
            if not own:
                continue
            files = {s["name"]: s for s in self.stickers.approved(m.id)}
            items = [
                {
                    "name": n,
                    "caption": files[n]["caption"],
                    "url": f"/api/stickers/{quote(m.id)}/{quote(n)}/image?v={files[n]['file'][:8]}",
                }
                for n in own
            ]
            packs.append({"pack": m.name, "member": m.id, "items": items})
        packs += self.packs.chat_packs({m.id: m.name for m in self.chats.everyone()})
        return {"packs": packs}

    # --- 外部表情包（dsh-meme 格式） -------------------------------------------------------

    def _packs_call(self, fn: Any, *args: Any, **kw: Any) -> Any:
        try:
            return fn(*args, **kw)
        except PackError as exc:
            raise ServiceError(str(exc)) from None

    def sticker_packs(self) -> dict[str, Any]:
        """本机装的外部表情包。"""
        return {"packs": [self.packs.summary(p) for p in self.packs.installed()]}

    def sticker_pack_catalog(self) -> dict[str, Any]:
        return {"packs": self._packs_call(self.packs.catalog)}

    def install_sticker_pack(
        self, source: str, owner: str | None = None, label: str = "", data: bytes | None = None
    ) -> dict[str, Any]:
        return self._packs_call(self.packs.install, source, owner, label, data)

    def assign_sticker_pack(
        self, pack_id: str, owner: str | None = None, label: str | None = None
    ) -> dict[str, Any]:
        return self._packs_call(self.packs.assign, pack_id, owner, label)

    def remove_sticker_pack(self, pack_id: str) -> dict[str, Any]:
        return self._packs_call(self.packs.remove, pack_id)

    def sticker_pack_image(self, pack_id: str, n: int) -> tuple[bytes, str]:
        try:
            return self.packs.image(pack_id, n)
        except PackError as exc:
            raise ServiceError(str(exc), 404) from None

    def chat_personas(self) -> dict[str, Any]:
        """每位成员的聊天人设；source：local 网页里改过 / config 配置文件 / empty 没有设定。"""
        out = []
        for m in self.chats.everyone():
            persona = persona_of(self.rt, m.id)
            if self.rt.repo.member_persona(m.id) is not None:
                source = "local"
            elif not is_empty(persona):
                source = "config"
            else:
                source = "empty"
            out.append(
                {"id": m.id, "name": m.name, "persona": persona.model_dump(), "source": source}
            )
        return {"members": out}

    def chat_set_persona(self, member: str, data: dict[str, Any] | None) -> dict[str, Any]:
        self._chat_member(member)
        try:
            persona = set_persona(self.rt, member, data)
        except ValueError as exc:
            raise ServiceError(f"人设不合法：{exc}") from None
        return {"id": member, "persona": persona.model_dump()}

    def chat_memories(self, member: str | None = None) -> dict[str, Any]:
        names = {m.id: m.name for m in self.chats.everyone()}
        if member is not None:
            self._chat_member(member)
        rows = self.rt.repo.memories(member)
        return {
            "memories": [{**r, "name": names.get(r["member"], r["member"])} for r in rows],
            "layers": list(LAYERS),
        }

    def _memory_text(self, text: str) -> str:
        limit = self.rt.config.roundtable.chat.memory.entry_chars
        clean = clean_memory(text, limit)
        if not clean:
            raise ServiceError("记忆不能为空（含密码、证件号、银行卡等敏感内容的行不会保存）")
        return clean

    def chat_add_memory(self, member: str, text: str) -> dict[str, Any]:
        """用户替成员记一条（随手记，source = user）；也记成一件事实（不变淡）。"""
        self._chat_member(member)
        row = self.rt.repo.add_memory(member, "note", self._memory_text(text), source="user")
        self.memory.store.write(
            member,
            row["text"],
            day=self._local_now().date(),
            fact=True,
            importance=2,
            source="user",
            source_key=f"note:{row['id']}",
        )
        return row

    def chat_update_memory(self, memory_id: int, text: str) -> dict[str, Any]:
        old = self.rt.repo.memory(memory_id)
        if old is None:
            raise ServiceError("没有这条记忆", 404)
        row = self.rt.repo.update_memory(memory_id, self._memory_text(text)) or {}
        item = self.rt.repo.memory_item_by_key(old["member"], f"note:{memory_id}")
        if item is not None and row:
            self.memory.store.user_edit(item["id"], row["text"])
        return row

    def chat_delete_memory(self, memory_id: int) -> dict[str, Any]:
        old = self.rt.repo.memory(memory_id)
        if old is None or not self.rt.repo.delete_memory(memory_id):
            raise ServiceError("没有这条记忆", 404)
        item = self.rt.repo.memory_item_by_key(old["member"], f"note:{memory_id}")
        if item is not None:
            self.rt.repo.delete_memory_item(item["id"])
        return {"deleted": 1}

    def chat_clear_memories(self, member: str | None = None) -> dict[str, Any]:
        """一键清空：日记和分层记忆，加上记忆件、挂着的事、联想网、习惯、翻日记记录和训练数据。"""
        if member is not None:
            self._chat_member(member)
        n = self.rt.repo.clear_memories(member)
        return {"deleted": n, "cleared": self.rt.repo.clear_memory_p2(member)}

    # --- 记忆与联想（P2）：记忆件、挂着的事、习惯、翻日记 ----------------------------

    def chat_memory_items(
        self, member: str, q: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """一位成员的记忆件（q 为空时最近的在前，否则按全文索引找）、挂着的事、习惯、统计。"""
        self._chat_member(member)
        repo = self.rt.repo
        if self.rt.config.roundtable.chat.memory.recall == "half_life":
            prepare_items(self.rt)
        limit = min(max(limit, 1), 500)
        if q:
            ids = repo.search_memory_items(member, query_terms(q), limit)
            items = sorted(repo.memory_items_by_ids(ids), key=lambda r: r["last_at"], reverse=True)
        else:
            items = repo.memory_items(member, current=False, limit=limit)
        digest = repo.memory_digest(member)
        return {
            "items": items,
            "hanging": repo.memory_hanging(member),
            "habits": digest["text"] if digest else "",
            "count": repo.count_memory_items(member),
            "links": repo.count_memory_links(member),
            "diary_reads_left": DiaryTool(self.rt).remaining(member),
            "domains": list(self.rt.config.roundtable.chat.memory.domains),
        }

    def chat_memory_item_history(self, item_id: int) -> dict[str, Any]:
        item = self.rt.repo.memory_item(item_id)
        if item is None:
            raise ServiceError("没有这件记忆", 404)
        return {"item": item, "revisions": self.rt.repo.memory_revisions(item_id)}

    def chat_update_memory_item(self, item_id: int, text: str) -> dict[str, Any]:
        if self.rt.repo.memory_item(item_id) is None:
            raise ServiceError("没有这件记忆", 404)
        return self.memory.store.user_edit(item_id, self._memory_text(text)) or {}

    def chat_delete_memory_item(self, item_id: int) -> dict[str, Any]:
        if not self.rt.repo.delete_memory_item(item_id):
            raise ServiceError("没有这件记忆", 404)
        return {"deleted": 1}

    def chat_drop_hanging(self, hanging_id: int) -> dict[str, Any]:
        if not self.rt.repo.close_memory_hanging(hanging_id, "dropped"):
            raise ServiceError("没有这件挂着的事", 404)
        return {"dropped": 1}

    def chat_read_diary(self, member: str, q: str) -> dict[str, Any]:
        """你在网页上翻成员的日记（不占成员每天翻日记的次数）。"""
        self._chat_member(member)
        return {"found": DiaryTool(self.rt).find(member, q)}

    async def chat_tidy_memories(self) -> dict[str, Any]:
        """马上整理一次记忆（到昨天为止还没整理的日子）。"""
        now = self._local_now()
        return {"written": await self.memory.catch_up(now)}

    # --- 上网（订阅源） ------------------------------------------------------------
    # --- 成员引擎：自我档案（docs/MEMBER_ENGINE.md） ---------------------------------

    def _profile_member(self, member: str) -> str:
        if member not in {m.id for m in self.chats.everyone()}:
            raise ServiceError(f"没有这位成员：{member}")
        return member

    def member_profile(self, member: str) -> dict[str, Any]:
        """当前档案、版本号和修改记录（新的在前）。"""
        p = self.engine.profiles.current(self._profile_member(member))
        rules = self.rt.config.roundtable.chat.engine
        card = card_text(self.rt, member)
        return {
            "member": member,
            "version": p.version,
            "data": p.data,
            "locked": p.locked,
            "sections": dict(rules.profile.sections),
            "history": self.engine.profiles.history(member),
            "card": {
                "text": card,
                "chars": len(card),
                "budget": rules.card.budget_chars,
                "custom": bool(str(p.get("identity.随身卡") or "").strip()),
            },
        }

    def member_profiles(self) -> dict[str, Any]:
        """每位成员的档案概况：版本号、随身卡字数（网页「自我档案」的成员列表）。"""
        out = []
        for m in self.chats.everyone():
            p = self.engine.profiles.current(m.id)
            card = card_text(self.rt, m.id)
            out.append(
                {
                    "id": m.id,
                    "name": m.name,
                    "version": p.version,
                    "nickname": p.get("identity.小名") or "",
                    "card_chars": len(card),
                }
            )
        return {"members": out, "budget": self.rt.config.roundtable.chat.engine.card.budget_chars}

    def member_profile_import(
        self, text: str, mapping: dict[str, str] | None = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """把人物卡（Markdown，每位成员一节）读进自我档案；以用户身份写，每位成员存一个新版本。

        卡名和成员显示名一样就自动对上，mapping（卡名 → 成员 id，空串 = 跳过）可以改。
        dry_run 只解析不写。用户锁住的项不动。
        """
        cards = parse_cards(text)
        if not cards:
            raise ServiceError("没认出人物卡：每位成员一节，标题写成「## 名字 · 完整角色卡」")
        everyone = self.chats.everyone()
        ids = {m.id for m in everyone}
        matched = match_members(cards, [(m.id, m.name) for m in everyone])
        for name, mid in (mapping or {}).items():
            if name in matched:
                matched[name] = mid or None
        source = source_title(text)
        out = []
        for card in cards:
            mid = matched.get(card.name)
            if mid is not None and mid not in ids:
                raise ServiceError(f"没有这位成员：{mid}")
            row: dict[str, Any] = {
                "name": card.name,
                "member": mid,
                "chapters": list(card.chapters),
                "version": None,
            }
            if mid is not None and not dry_run:
                patch = card_patch(card, source)
                patch = without_locked(patch, self.engine.profiles.current(mid).locked)
                self._profile_do(
                    lambda mid=mid, patch=patch: self.engine.profiles.change(
                        mid, patch, author="user", reason=f"导入人物卡：{source}"
                    )
                )
                row["version"] = self.engine.profiles.current(mid).version
            out.append(row)
        return {
            "source": source,
            "cards": out,
            "members": [{"id": m.id, "name": m.name} for m in everyone],
            "imported": sum(1 for r in out if r["version"] is not None),
        }

    def _profile_do(self, fn: Any) -> None:
        try:
            fn()
        except ProfileError as e:
            raise ServiceError(str(e)) from None

    def member_profile_change(
        self, member: str, patch: dict[str, Any], reason: str = ""
    ) -> dict[str, Any]:
        """用户改档案：{分区: {键: 值或 None}}。"""
        member = self._profile_member(member)
        self._profile_do(
            lambda: self.engine.profiles.change(
                member, patch, author="user", reason=reason or "你改的"
            )
        )
        return self.member_profile(member)

    def member_profile_lock(self, member: str, path: str, locked: bool = True) -> dict[str, Any]:
        member = self._profile_member(member)
        self._profile_do(lambda: self.engine.profiles.lock(member, path, locked=locked))
        return self.member_profile(member)

    def member_profile_revert(self, member: str, version: int) -> dict[str, Any]:
        member = self._profile_member(member)
        self._profile_do(lambda: self.engine.profiles.revert(member, version))
        return self.member_profile(member)

    def member_profile_clear(self, member: str) -> dict[str, Any]:
        member = self._profile_member(member)
        n = self.engine.profiles.clear(member)
        return {"member": member, "deleted": n}

    def member_states(self) -> dict[str, Any]:
        """每位成员此刻的心情（-1～1）和精力（0～1）、基线、最近的本能反射（P3 状态，只读）。"""
        state = self.engine.state
        out = []
        for m in self.chats.everyone():
            r = state.read(m.id)
            b = r.baseline
            out.append(
                {
                    "member": m.id,
                    "name": m.name,
                    "mood": round(r.mood, 3),
                    "energy": round(r.energy, 3),
                    "line": state.line(m.id, r.at),
                    "baseline": {
                        "mood": b.mood,
                        "sensitivity": b.sensitivity,
                        "wake": b.wake,
                        "sleep": b.sleep,
                        "night_owl": b.night_owl,
                    },
                    "recent": r.recent,
                }
            )
        return {"members": out, "enabled": state.enabled()}

    def member_states_clear(self, member: str | None = None) -> dict[str, Any]:
        """清空状态（全部或某一位），回到基线。"""
        return {"deleted": self.engine.state.clear(member)}

    # --- 成员关系（P4，docs/MEMBER_ENGINE.md「P4 关系」） ------------------------------

    def member_relation(self, member: str) -> dict[str, Any]:
        """和你的阶段、亲近度，和每位同事的亲近度与看法（程序按共同经历算，只读）。"""
        member = self._profile_member(member)
        rel = self.engine.relation
        rules = self.rt.config.roundtable.chat.engine.relation
        data = rel.data(member)
        live = {f["id"] for f in self.engine.about.facts()}
        facts = {f["id"]: f["text"] for f in self.engine.about.facts()}
        views = [
            {
                **v,
                "index": i,
                "live": view_live(v, live),
                "facts_text": [facts[n] for n in v.get("facts") or [] if n in facts],
            }
            for i, v in enumerate(data.get("views") or [])
        ]
        due = self.insight.due(self._local_now().date())
        depth = rules.insight.at(rel.level(member))
        return {
            **rel.summary(member),
            "depth": {"views": depth.views, "chars": depth.chars},
            "enabled": rel.enabled(),
            "stages": [s.model_dump() for s in rules.stages],
            "views": views,
            "views_week": rel.views_week(member),
            "views_log": list(reversed(data.get("views_log") or []))[:5],
            "insight_due": week_id(due) if due else None,
            "insight_enabled": self.insight.enabled(),
        }

    async def member_relation_insight(self, member: str) -> dict[str, Any]:
        """马上让她想一次上一周对你的看法（每周一次；已想过返回 409）。"""
        member = self._profile_member(member)
        due = self.insight.due(self._local_now().date())
        if due is None or not self.insight.enabled():
            raise ServiceError("还没到每周想一想的那天", 409)
        wid = week_id(due)
        if self.engine.relation.views_week(member) == wid:
            raise ServiceError(f"{wid} 已经想过了", 409)
        m = next(m for m in self.chats.everyone() if m.id == member)
        got = await self.insight.run(m, wid, today=self._local_now().date())
        if got is None:
            raise ServiceError("今天生活的额度不够了，下次再想", 409)
        return {"result": got, **self.member_relation(member)}

    def member_relation_drop_view(self, member: str, index: int) -> dict[str, Any]:
        """删掉她的一句见解。"""
        member = self._profile_member(member)
        if not self.engine.relation.drop_view(member, index):
            raise ServiceError("没有这一句", 404)
        return self.member_relation(member)

    def member_relation_reply_view(self, member: str, index: int, text: str) -> dict[str, Any]:
        """你在她的一句看法下随手回一句（空串＝撤回）；她下周自己琢磨你的意思。"""
        member = self._profile_member(member)
        if not self.engine.relation.reply_view(member, index, text):
            raise ServiceError("没有这一句", 404)
        return self.member_relation(member)

    def member_relation_clear(self, member: str | None = None) -> dict[str, Any]:
        """清空关系（全部或某一位），从头再按经历累积。"""
        if member is not None:
            member = self._profile_member(member)
        return {"deleted": self.engine.relation.clear(member)}

    # --- 关于你（成员系统 P8：8 位共用的用户画像，只存本机） --------------------------------

    def _about(self, fn: Any) -> Any:
        try:
            return fn()
        except AboutMissing as e:
            raise ServiceError(str(e), 404) from None
        except AboutError as e:
            raise ServiceError(str(e)) from None

    def about_view(self) -> dict[str, Any]:
        """画像：每条的领域、来源、把握度、变淡 / 锁定，证据（原话、会话、时间、谁注意到的）。"""
        v = self.engine.about.view()
        names = {m.id: m.name for m in self.chats.everyone()}
        rooms = {r["id"]: r for r in self.rt.repo.chat_rooms()}
        for f in v["facts"]:
            for e in f["evidence"]:
                room = rooms.get(e["room"])
                e["room_title"] = (
                    "私聊" if room and room["kind"] == "dm" else (room or {}).get("title", "")
                )
                e["by_name"] = names.get(e.get("by"), e.get("by") or "")
            f["noticed"] = [names.get(x, x) for x in f.get("noticed", [])]
        return v

    # 你的状态的来源（每项只在开关打开、成员要回话时才问；读到的不写库）

    def _say_music(self, now: datetime) -> str | None:
        v = self._np().view(self.engine.about.switches()["music_player"])
        cur = v.get("now")
        if not v["available"] or not cur or cur["status"] != "playing" or not cur["title"]:
            return None
        who = f"（{cur['artist']}）" if cur["artist"] else ""
        return f"用户正在听《{cur['title']}》{who}"

    def _say_idle(self, now: datetime) -> str | None:
        if self._pc.available():
            return None
        idle = self._pc.read().idle_seconds
        if idle is None:
            return None
        minutes = int(idle // 60)
        if minutes >= self.engine.about.rules.idle_minutes:
            return f"用户的电脑已经 {minutes} 分钟没人动了（可能走开了）"
        return "用户正在用电脑"

    def _say_game(self, now: datetime) -> str | None:
        if self._pc.available():
            return None
        game = game_of(self._pc.read().foreground, self.engine.about.games())
        return f"用户在玩{game}" if game else None

    def _say_mail(self, now: datetime) -> str | None:
        if not self.mail.enabled():
            return None
        d = self.mail.view()["digest"]
        text = f"用户邮箱今天收到 {d['today']} 封，重要未读 {d['important_unread']} 封"
        if d["due"]:
            first = d["due"][0]
            text += f"；最近截止 {first['due'][5:10]}「{' '.join(first['snippet'].split())[:16]}」"
        return text

    def about_status(self) -> dict[str, Any]:
        return self.rt.user_status.view()

    def about_status_set(self, kind: str, on: bool) -> dict[str, Any]:
        self._about(lambda: self.engine.about.set_status(kind, on))
        return self.about_status()

    def about_music_player(self, player: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.set_music_player(player))
        return self.about_status()

    def about_game_add(self, exe: str, name: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.add_game(exe, name))
        return self.about_status()

    def about_game_del(self, exe: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.del_game(exe))
        return self.about_status()

    def about_class_add(self, weekday: int, start: str, end: str, name: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.add_class(weekday, start, end, name))
        return self.about_status()

    def about_ddl_add(self, name: str, due: str) -> dict[str, Any]:
        self._about(lambda: self.engine.about.add_ddl(name, due))
        return self.about_status()

    def about_schedule_del(self, kind: str, item_id: int) -> dict[str, Any]:
        self._about(lambda: self.engine.about.del_schedule(kind, item_id))
        return self.about_status()

    def about_schedule_clear(self) -> dict[str, Any]:
        return {"deleted": self.engine.about.clear_schedule()}

    def about_add(self, text: str, domain: str | None = None) -> dict[str, Any]:
        self._about(lambda: self.engine.about.add(text, domain))
        return self.about_view()

    def about_edit(
        self, fact_id: int, text: str | None = None, domain: str | None = None
    ) -> dict[str, Any]:
        self._about(lambda: self.engine.about.edit(fact_id, text, domain))
        return self.about_view()

    def about_delete(self, fact_id: int) -> dict[str, Any]:
        self._about(lambda: self.engine.about.delete(fact_id))
        return self.about_view()

    def about_reject(self, fact_id: int) -> dict[str, Any]:
        """「不对」：删掉，之后像它的线索不再记。"""
        self._about(lambda: self.engine.about.reject(fact_id))
        return self.about_view()

    def about_forget_rejected(self, index: int) -> dict[str, Any]:
        self._about(lambda: self.engine.about.forget_rejected(index))
        return self.about_view()

    def about_lock(self, fact_id: int, locked: bool = True) -> dict[str, Any]:
        self._about(lambda: self.engine.about.lock(fact_id, locked))
        return self.about_view()

    def about_domain(self, domain: str, on: bool) -> dict[str, Any]:
        self._about(lambda: self.engine.about.set_domain(domain, on))
        return self.about_view()

    def about_clear(self, rejected: bool = False) -> dict[str, Any]:
        return {"deleted": self.engine.about.clear(rejected)}

    def about_export(self, fmt: str = "json") -> tuple[bytes, str, str]:
        """导出画像：(内容, 类型, 文件名)；fmt = json / md。"""
        if fmt not in ("json", "md"):
            raise ServiceError("格式只能是 json 或 md")
        text = self.engine.about.export(fmt)
        media = "application/json" if fmt == "json" else "text/markdown"
        return text.encode("utf-8"), f"{media}; charset=utf-8", f"about_you.{fmt}"

    # --- 成员成长（P6，docs/MEMBER_ENGINE.md「P6 成长」） ------------------------------

    def member_growth(self, member: str) -> dict[str, Any]:
        """成长时间线（新的在前）、上次反思的是哪一周、下一次反思哪一周。"""
        member = self._profile_member(member)
        rules = self.rt.config.roundtable.chat.engine.growth
        due = self.growth.due(self._local_now().date())
        names = {m.id: m.name for m in self.chats.everyone()}
        entries = []
        for e in self.growth.log.entries(member):
            e = dict(e)
            if e.get("clash"):
                e["clash_name"] = names.get(e["clash"], e["clash"])
            entries.append(e)
        return {
            "member": member,
            "enabled": rules.enabled,
            "week": self.growth.log.done_week(member),
            "due": week_id(due) if due else None,
            "rules": {
                "max_changes": rules.max_changes,
                "core_monthly_steps": rules.core_monthly_steps,
                "user_share": rules.user_share,
                "min_days": rules.min_days,
            },
            "timeline": entries,
            # 成长总账：设定、口癖、和你的阶段、对你的看法、收藏排成一条（新的在前）
            "book": self.growth.book.changes(member) if rules.book.enabled else [],
            "book_sources": BOOK_SOURCES,
            "habits": self.growth.habits(member),
            "skills": self.growth.skills(member),
            # 发育期可塑性（成长 G2）：新来的变得快，待久了变得慢
            "plastic": plasticity(
                self.rt, member, self._local_now().date(), self.growth.profiles.current(member).data
            ).line()
            if rules.plastic.enabled
            else "",
            "reports": [
                {**r, "lines": report_lines(r)}
                for _, r in sorted(self.growth.log.load(member)["reports"].items(), reverse=True)
            ],
        }
