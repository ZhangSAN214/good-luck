"""旅行打卡（L5，借 MIT 项目 dsh-bili-lvxingfeiyu 的思路，只借思路、不搬代码）。只用于生活。

一次打卡 = 一个地方的一刻（旅行途中的一件事，或看过的 B 站视频里的一段）：

1. **判定卡**（``checkin_card`` 提示词，一次调用，只输出 JSON）：值不值得「站进去」。命中排除项
   （文字页、正脸特写、人群、已有动漫角色、水印、空无一事）或分数不到 ``MIN_SCORE`` → 不打卡，
   只记一笔为什么（**不硬出图、不硬写**）。动作偷懒（「提着裙摆站着」「摆个姿势」）也算不过。
2. **旅行日记**（``travel_journal``）：判定卡过了才写，第一人称，带她自己**前 3 天**打卡的
   上下文（去过哪、那一刻在发生什么、她在那儿做了什么、日记标题），前后连得上。
3. **配图**（默认关）：站位、远近、朝向、动作、神情和固定的一致性约束由**代码拼成结构化的拍法**
   （``shot_text``，模型改不动），交给朋友圈配图同一套绘图（成员形象参考图）。开关在
   ``member_checkin_photo``；真出图前先问用户（2026-11-02 任务要求）。

额度写死在代码里（``PER_DAY`` / ``PER_MEMBER_DAY``）；花费记在 ``travel`` 步骤，算生活每日上限
（$0.15 不变），每次调用前问 ``allow``。不碰办公室：不读会话、不写档案。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from roundtable.core.chat import ChatMember, call_member, member_memory
from roundtable.core.jsonout import JSONOutputError, extract_json_object
from roundtable.core.member.checkin import store
from roundtable.core.persona import chat_persona
from roundtable.core.plain_talk import plain_prompt

STEP = "travel"  # 调用记在这个步骤上（life.LIFE_STEPS 里，算生活每日上限）
ROOM = "travel"  # 隐藏会话
PER_DAY = 3  # 全体每天最多打几次卡（含没过的判定）
PER_MEMBER_DAY = 1
MIN_SCORE = 12  # 判定卡分数（0～20）不到这个不打卡
CONTEXT_DAYS = 3  # 旅行日记带前几天的打卡
CARD_USD = 0.003
JOURNAL_USD = 0.006
JOURNAL_TOKENS = 2500
JOURNAL_CHARS = 1500
SOURCES = {"trip": "这趟旅行", "bili": "刚看的 B 站视频"}

# 结构化拍法（写在代码里，模型只填判定卡的字段，改不动这些约束）
STAND = {"left": "她站在画面左侧", "center": "她站在画面正中", "right": "她站在画面右侧"}
DISTANCE = {"near": "近景（半身）", "mid": "中景（全身，留出周围环境）", "far": "远景（人很小）"}
FACING = {"back": "背对镜头", "side": "侧身", "front": "面向镜头"}
FIXED = (
    "人物外观和参考图一致、正常人体比例；正好两条手臂、两只手，每只手五根手指；"
    "头和脖子、身体自然相连；光影跟着场景走，影子落在对的地方；"
    "不重画背景、不新增别的人物；画面里不出现文字和水印"
)
LAZY = ("提着裙摆", "提裙摆", "摆了个", "摆个", "站在那里", "站着不动", "可爱的姿势")
WINDY = ("风", "水", "浪", "台阶", "楼梯", "雨", "海")
_TITLE = re.compile(r"^\s*#\s*(.+)$")


@dataclass
class Scene:
    """要判的一刻。moments：几行「[时刻] 画面 / 在发生的事」。"""

    member: str
    source: str  # trip / bili
    title: str  # 目的地 / 视频标题
    moments: list[str]
    at: datetime
    url: str = ""
    ref: str = ""  # 来源记录的 id（生活事件 / 看过的视频）
    extra: dict[str, Any] = field(default_factory=dict)


def clean_card(data: dict[str, Any], scene_text: str) -> tuple[dict[str, Any], str]:
    """校验判定卡：返回 (整理后的卡, 不过的原因；过了为空)。"""

    def s(key: str, n: int) -> str:
        v = data.get(key)
        return str(v).strip()[:n] if v not in (None, "") else ""

    card = {
        "can": data.get("can") is True,
        "why": s("why", 60),
        "reject": s("reject", 40) or None,
        "line": s("line", 60),
        "action": s("action", 60),
        "emotion": s("emotion", 8),
        "stand": data.get("stand") if data.get("stand") in STAND else "right",
        "distance": data.get("distance") if data.get("distance") in DISTANCE else "mid",
        "facing": data.get("facing") if data.get("facing") in FACING else "side",
        "score": max(0, min(20, int(data["score"]) if isinstance(data.get("score"), int) else 0)),
        "prompt": s("prompt", 60),
    }
    if not card["can"]:
        return card, card["reject"] or "没有想站进去的地方"
    if not card["action"] or not card["line"]:
        return card, "判定卡没写动作或那一刻在发生什么"
    lazy = any(w in card["action"] for w in LAZY)
    if lazy and not any(w in scene_text for w in WINDY):
        return card, "动作偷懒（和画面内容无关）"
    if card["score"] < MIN_SCORE:
        return card, f"没那么想去（{card['score']}/20）"
    return card, ""


def shot_text(card: dict[str, Any]) -> str:
    """判定卡 → 结构化拍法（代码拼，固定约束在最后）。"""
    parts = [
        STAND[card["stand"]],
        DISTANCE[card["distance"]],
        FACING[card["facing"]],
        f"她在做的事：{card['action']}",
    ]
    if card.get("emotion"):
        parts.append(f"神情：{card['emotion']}")
    if card.get("prompt"):
        parts.append(f"细节：{card['prompt']}")
    return "；".join(parts) + "。" + FIXED


def split_journal(text: str) -> tuple[str, str]:
    """日记 → (标题, 正文)。第一行不是标题时取正文前十几个字当标题。"""
    lines = [x for x in (text or "").strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", ""
    m = _TITLE.match(lines[0])
    if m:
        title, body = m.group(1).strip(), "\n".join(lines[1:]).strip()
    else:
        body = "\n".join(lines).strip()
        title = re.split(r"[，。！？\n]", body, maxsplit=1)[0][:16]
    return title[:30], body[:JOURNAL_CHARS]


class Checkin:
    def __init__(self, rt: Any, allow: Callable[[float], bool]) -> None:
        self.rt = rt
        self.allow = allow

    def enabled(self) -> bool:
        life = self.rt.config.roundtable.chat.life
        return life.enabled and life.story.enabled and life.story.travel.enabled

    def photo_on(self) -> bool:
        return store.switch(self.rt, store.PHOTO_KEY, False)

    # --- 读 -------------------------------------------------------------------------

    def records(self, member: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = store.checkins(self.rt)
        if member:
            rows = [r for r in rows if r["member"] == member]
        return list(reversed(rows[-limit:]))

    def today(self, at: datetime) -> list[dict[str, Any]]:
        d = at.date().isoformat()
        return [r for r in store.checkins(self.rt) if r.get("date") == d]

    def room_left(self, member: str, at: datetime) -> bool:
        rows = self.today(at)
        return len(rows) < PER_DAY and sum(r["member"] == member for r in rows) < PER_MEMBER_DAY

    def context(self, member: str, at: datetime) -> list[dict[str, Any]]:
        """她前 CONTEXT_DAYS 天打过的卡（过了的，按时间先后）。"""
        lo = (at - timedelta(days=CONTEXT_DAYS)).isoformat()
        hi = at.isoformat()
        return [
            r
            for r in store.checkins(self.rt)
            if r["member"] == member and r.get("ok") and lo <= r["at"] < hi
        ]

    # --- 打卡 -----------------------------------------------------------------------

    async def run(self, member: ChatMember, scene: Scene) -> dict[str, Any] | None:
        """判一次；过了写日记（开了配图再画一张）。没钱、额度用完、调用失败时为 None。"""
        if not self.enabled() or not self.room_left(member.id, scene.at):
            return None
        if not self.allow(CARD_USD):
            return None
        moments = "\n".join(scene.moments) or "（没有记下什么）"
        data = await self._call(
            member,
            "checkin_card",
            CARD_USD,
            source=SOURCES.get(scene.source, scene.source),
            title=scene.title,
            moments=moments,
        )
        if data is None:
            return None
        try:
            raw = extract_json_object(data)
        except JSONOutputError:
            raw = {"can": False, "reject": "判定卡不是 JSON"}
        card, reason = clean_card(raw, moments)
        rid = hashlib.sha1(
            f"{member.id}:{scene.source}:{scene.ref}:{scene.at}".encode()
        ).hexdigest()
        rec: dict[str, Any] = {
            "id": rid[:12],
            "member": member.id,
            "source": scene.source,
            "title": scene.title,
            "url": scene.url,
            "ref": scene.ref,
            "at": scene.at.isoformat(),
            "date": scene.at.date().isoformat(),
            "card": card,
            "ok": not reason,
            "reason": reason,
            "journal": None,
            "photo": None,
        }
        if not reason:
            rec["journal"] = await self.journal(member, rec)
            if rec["journal"] and self.photo_on():
                rec["photo"] = await self.photo(member, rec)
        rows = store.checkins(self.rt)
        rows.append(rec)
        store.save_checkins(self.rt, rows)
        return rec

    async def journal(self, member: ChatMember, rec: dict[str, Any]) -> dict[str, str] | None:
        if not self.allow(JOURNAL_USD):
            return None
        card = rec["card"]
        facts = [
            f"- 日期：{rec['date']}",
            f"- 地方：{SOURCES.get(rec['source'], rec['source'])}《{rec['title']}》",
            f"- 那一刻：{card['line']}",
            f"- 我在那儿做的事：{card['action']}",
        ]
        if card.get("why"):
            facts.append(f"- 为什么想站进去：{card['why']}")
        if card.get("emotion"):
            facts.append(f"- 当时的心情：{card['emotion']}")
        past = self.context(member.id, datetime.fromisoformat(rec["at"]))
        lines = []
        for r in past:
            c = r["card"]
            title = (r.get("journal") or {}).get("title", "")
            lines.append(
                f"- {r['date']}《{r['title']}》"
                + (f" 日记题目「{title}」" if title else "")
                + (f" 那一刻：{c['line'][:40]}" if c.get("line") else "")
                + (f" 我在那儿：{c['action'][:40]}" if c.get("action") else "")
            )
        text = await self._call(
            member,
            "travel_journal",
            JOURNAL_USD,
            tokens=JOURNAL_TOKENS,
            facts="\n".join(facts),
            context="\n".join(lines) or "（前几天没有出门打卡）",
            memory=member_memory(self.rt, member.id, f"{rec['title']} {card['line']}"),
        )
        if not text:
            return None
        title, body = split_journal(text)
        if not body:
            return None
        return {"title": title, "body": body}

    async def photo(self, member: ChatMember, rec: dict[str, Any]) -> dict[str, Any] | None:
        """配图（默认关）：结构化拍法 + 成员形象参考图，走朋友圈配图同一套绘图和每月上限。"""
        from roundtable.core.chat_actions import generated_item
        from roundtable.core.chat_media import ChatMediaStore, MediaError
        from roundtable.core.stickers.member import (
            MemberStickers,
            StickerBudgetExceeded,
            StickerError,
            StickerUnavailable,
        )

        rt = self.rt
        if rt.chat_media_dir is None or member.id not in rt.config.personas.sticker_cards:
            return None
        card = rec["card"]
        try:
            data, mime = await MemberStickers(rt, rt.sticker_dir).photo(
                member.id,
                f"地点：{rec['title']}\n那一刻：{card['line']}",
                (rec.get("journal") or {}).get("title", ""),
                shot_text(card),
                refs=True,
                seed=rec["id"],
            )
            return generated_item(ChatMediaStore(rt.chat_media_dir), data, mime)
        except (StickerBudgetExceeded, StickerError, StickerUnavailable, MediaError, ValueError):
            return None

    async def _call(
        self, member: ChatMember, role: str, estimate: float, *, tokens: int = 0, **values: str
    ) -> str | None:
        rt = self.rt
        version = rt.config.roundtable.prompts[role]
        wants = rt.prompts.get(role, version).variables
        values = {"name": member.name, "persona": chat_persona(rt, member.id), **values}
        prompt = rt.prompts.render(role, version, **{k: v for k, v in values.items() if k in wants})
        prompt = plain_prompt(rt.prompts, rt.config, prompt)
        params = dict(rt.config.roundtable.step_params.get(STEP, {}))
        if tokens:
            params["max_tokens"] = tokens
        completion = await call_member(
            rt,
            room=ROOM,
            model_id=member.model_id,
            prompt=prompt,
            step=STEP,
            params=params,
            estimate_usd=estimate,
        )
        return completion.text if completion is not None else None
