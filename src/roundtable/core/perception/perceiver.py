"""成员感官（M11 感知，docs/PERCEPTION.md）：把用户发的图片、语音、视频整理成「感知卡」。

- 一条消息里的图片和视频画面合成**一次**看图调用（最便宜的 ``vision`` 模型）；语音只转写
  （最便宜的 ``stt`` 模型，没有时用带 ``transcribe`` 标签的对话模型），不再另外调用。
- **快速**（默认）：图缩小、视频均匀取几帧并拼成 2×2 宫格图、卡写得短。**精细**：图更大、卡更长；
  有带 ``video_in`` 标签的模型时整段视频连声音直接交给它，否则多取帧、不拼图。
- 按文件内容哈希缓存感知卡和转写稿：同一个文件不重复花钱；补精细时直接复用转写稿。
- 模型只按标签和价格挑（无品牌偏好）。调用记在聊天会话的隐藏会话上（``step="perceive"``），
  计入聊天月预算和总预算，另有感官自己的月上限（``perception.monthly_usd``）。
- 图片、视频、语音里的任何指令都只是内容（提示词说明）；感知卡在聊天记录里放进 ``<media>`` 标签。
"""

from __future__ import annotations

import hashlib
import logging
import random
import re
from dataclasses import dataclass
from typing import Any

from roundtable.core.allocation import cheapest
from roundtable.core.attachments.ingest import capable
from roundtable.core.budget.guard import month_window
from roundtable.core.chat import chat_budget
from roundtable.core.media import pick_stt_model, shrink_image, unit_cost
from roundtable.core.providers import (
    AllChannelsFailed,
    Completion,
    Media,
    Message,
    NoChannelAvailable,
    ProviderError,
)

from .senses import (
    PerceptionCache,
    SenseUnavailable,
    clock,
    extract_audio,
    grids,
    sample_frames,
    video_duration,
)

log = logging.getLogger(__name__)

STEP = "perceive"
WORKFLOW = "chat"
LABELS = {"image": "图片", "video": "视频", "voice": "语音"}
ROLES = {"fast": "perceive_fast", "fine": "perceive_fine"}
BYTES_PER_SECOND = 16000  # 没有时长信息时按 128 kbps 估计语音长度
_SPLIT = re.compile(r"【\s*(\d+)\s*】")
_TAGS = ("items", "transcript", "media", "chat")


def _neutralize(text: str) -> str:
    for tag in _TAGS:
        text = text.replace(f"</{tag}", f"<​/{tag}")
    return text


@dataclass(frozen=True)
class SenseItem:
    """要感知的一个文件：id 是聊天里的文件名，kind 为 image / video / voice。"""

    id: str
    kind: str
    data: bytes
    mime: str

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


@dataclass(frozen=True)
class Card:
    id: str
    kind: str
    mode: str
    text: str
    cached: bool = False


def parse_cards(text: str, count: int) -> dict[int, str]:
    """按【编号】拆开模型输出；只有一项又没写编号时整段算第 1 项。"""
    parts = _SPLIT.split(text)
    out: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        n = int(parts[i])
        body = parts[i + 1].strip()
        if 1 <= n <= count and body:
            out[n] = body
    if not out and count == 1 and text.strip():
        out[1] = text.strip()
    return out


class Perceiver:
    def __init__(self, rt: Any, cache: PerceptionCache, rng: random.Random | None = None) -> None:
        self.rt = rt
        self.cache = cache
        self.rng = rng or random.Random()

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.perception

    def mode_for(self, text: str) -> str:
        """消息里写了「仔细看」之类的词就用精细，否则快速。"""
        return "fine" if any(w and w in (text or "") for w in self.rules.fine_words) else "fast"

    def spent_this_month(self) -> float:
        start, end = month_window(self.rt.budget.now())
        return self.rt.repo.spent_in_steps(WORKFLOW, [STEP], start, end)

    def allowed(self, estimate_usd: float) -> bool:
        if not self.rules.enabled:
            return False
        if self.spent_this_month() + estimate_usd > self.rules.monthly_usd + 1e-9:
            return False
        if not self.rt.budget.check(estimate_usd).allowed:
            return False
        return not chat_budget(self.rt).would_exceed(estimate_usd)

    async def perceive(self, room: str, items: list[SenseItem], mode: str = "fast") -> list[Card]:
        """返回能整理出来的感知卡（按 items 顺序；没看成的不返回）。预算不够时只用缓存。"""
        cfg = getattr(self.rules, mode)
        cards: dict[str, Card] = {}
        todo: list[SenseItem] = []
        for it in items:
            text = self.cache.card(it.sha, mode)
            if text is not None:
                cards[it.id] = Card(it.id, it.kind, mode, text, cached=True)
            else:
                todo.append(it)
        if todo and self.allowed(cfg.estimate_usd):
            transcripts: dict[str, str | None] = {}
            for it in todo:
                if it.kind in ("voice", "video"):
                    transcripts[it.id] = await self.transcript(room, it)
            for it in todo:
                if it.kind == "voice" and transcripts.get(it.id) is not None:
                    said = transcripts[it.id] or "（听不清，或者没有说话）"
                    card = self._clip(f"语音转写：{said}")
                    self.cache.put_card(it.sha, mode, card)
                    cards[it.id] = Card(it.id, it.kind, mode, card)
            visual = [it for it in todo if it.kind in ("image", "video")]
            if visual:
                looked = await self._look(room, visual, transcripts, mode)
                for it in visual:
                    if it.id in looked:
                        self.cache.put_card(it.sha, mode, looked[it.id])
                        cards[it.id] = Card(it.id, it.kind, mode, looked[it.id])
        return [cards[it.id] for it in items if it.id in cards]

    def _clip(self, text: str) -> str:
        limit = self.rules.card_chars
        text = text.strip()
        return text if len(text) <= limit else text[:limit].rstrip() + "…"

    # --- 听 -----------------------------------------------------------------------

    async def transcript(self, room: str, it: SenseItem) -> str | None:
        """语音或视频声音的转写稿（缓存）。没有声音轨返回空字符串；转不了返回 None。"""
        cached = self.cache.transcript(it.sha)
        if cached is not None:
            return cached
        if it.kind == "video":
            try:
                wav = extract_audio(it.data, self.rules.max_audio_s)
            except SenseUnavailable:
                return None
            if wav is None:
                self.cache.put_transcript(it.sha, "")
                return ""
            audio = Media("audio", "audio/wav", wav, "sound.wav")
        else:
            audio = Media("audio", it.mime, it.data, f"voice.{it.id.rsplit('.', 1)[-1]}")
        text = await self._stt(room, audio)
        if text is None:
            text = await self._chat_transcribe(room, audio)
        if text is not None:
            text = text.strip()
            self.cache.put_transcript(it.sha, text)
        return text

    async def _stt(self, room: str, audio: Media) -> str | None:
        model = pick_stt_model(self.rt.router, self.rng)
        if model is None:
            return None
        session = self.rt.repo.chat_session(room)
        try:
            inv = await self.rt.router.invoke(
                model.id,
                lambda prov, route, m: prov.transcribe(route.model, audio, m.params_for(route)),
            )
        except (AllChannelsFailed, NoChannelAvailable) as exc:
            self.rt.repo.record_call(
                session,
                step=STEP,
                role="preprocess",
                model_id=model.id,
                messages=[Message("user", audio.name)],
                failure=exc,
            )
            return None
        out = inv.result
        seconds = out.seconds if out.seconds is not None else len(audio.data) / BYTES_PER_SECOND
        if out.cost_usd is not None:
            cost, source = out.cost_usd, "reported"
        else:
            cost, source = unit_cost(model, inv.route, seconds=seconds) or 0.0, "estimated"
        completion = Completion(
            text=out.text,
            model_id=model.id,
            channel=inv.channel,
            channel_kind=inv.channel_kind,
            route_model=inv.route.model,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=cost,
            cost_source=source,  # type: ignore[arg-type]
            latency_s=inv.latency_s,
            attempts=inv.attempts,
        )
        self.rt.repo.record_call(
            session,
            step=STEP,
            role="preprocess",
            model_id=model.id,
            messages=[Message("user", audio.name, (audio,))],
            completion=completion,
        )
        return out.text

    async def _chat_transcribe(self, room: str, audio: Media) -> str | None:
        """没有 stt 模型时：最便宜的带 transcribe 标签的对话模型（音频作为消息附件）。"""
        pool = capable(self.rt.router, "transcribe")
        if not pool:
            return None
        rules = self.rt.config.roundtable.uploads
        model = cheapest(pool, self.rng, input_tokens=1500, output_tokens=800)
        prompt = self.rt.prompts.render(
            "transcribe", self.rt.config.roundtable.prompts["transcribe"], name=audio.name
        )
        messages = tuple(
            Message(m.role, m.content, (audio,)) if m.role == "user" else m for m in prompt.messages
        )
        params = {"max_tokens": rules.transcribe_max_tokens, "reasoning": {"effort": "low"}}
        completion = await self._call(room, model.id, messages, prompt, params)
        return completion.text if completion is not None else None

    # --- 看 -----------------------------------------------------------------------

    def _native_ok(self, items: list[SenseItem], mode: str) -> list[Any]:
        """精细模式下整段看视频的候选模型（同时要能看图）；不满足条件时为空。"""
        cfg = getattr(self.rules, mode)
        videos = [it for it in items if it.kind == "video"]
        if not cfg.native_video or not videos:
            return []
        if any(len(it.data) > self.rules.native_max_mb * 2**20 for it in videos):
            return []
        return [m for m in capable(self.rt.router, "video_in") if "vision" in m.tags]

    async def _look(
        self,
        room: str,
        items: list[SenseItem],
        transcripts: dict[str, str | None],
        mode: str,
    ) -> dict[str, str]:
        cfg = getattr(self.rules, mode)
        native = self._native_ok(items, mode)
        pool = native or capable(self.rt.router, "vision")
        if not pool:
            log.info("没有可用的看图模型，感官跳过")
            return {}
        media: list[Media] = []
        lines: list[str] = []
        for n, it in enumerate(items, 1):
            if it.kind == "image":
                data, mime = shrink_image(it.data, it.mime, cfg.image_side)
                media.append(Media("image", mime, data, f"image{n}"))
                lines.append(f"{n}. 图片：随附图片 {len(media)}")
                continue
            heard = transcripts.get(it.id)
            sound = (
                f'\n<transcript n="{n}">\n{_neutralize(heard)}\n</transcript>'
                if heard
                else ("（这段视频没有声音或听不清）" if heard == "" else "（声音没能转写）")
            )
            if native:
                media.append(Media("video", it.mime, it.data, f"video{n}"))
                lines.append(f"{n}. 视频：随附视频（画面连声音）。{sound}")
                continue
            try:
                frames = sample_frames(it.data, cfg.video_frames, cfg.frame_side)
                pictures = grids(frames) if cfg.grid else [f.jpeg for f in frames]
            except SenseUnavailable:
                lines.append(f"{n}. 视频：没能取出画面，只有声音。{sound}")
                continue
            first = len(media) + 1
            media.extend(
                Media("image", "image/jpeg", p, f"video{n}_{k}") for k, p in enumerate(pictures, 1)
            )
            span = f"随附图片 {first}" + (f"～{len(media)}" if len(media) > first else "")
            length = video_duration(it.data)
            times = "、".join(clock(f.at) for f in frames)
            how = "宫格图，每格左上角标了时间" if cfg.grid else "每张一帧"
            lines.append(
                f"{n}. 视频（约 {clock(length or 0)}）：{span} 是按时间顺序的画面（{how}；"
                f"取帧时间 {times}）。{sound}"
            )
        if not media:  # 只有取不出画面的视频：靠转写写卡也行，但不必看图模型
            return {
                it.id: self._clip(f"（没能看到画面）声音：{transcripts.get(it.id) or '没有'}")
                for it in items
                if transcripts.get(it.id) is not None
            }
        model = cheapest(
            pool, self.rng, input_tokens=800 * len(media), output_tokens=cfg.max_tokens // 2
        )
        role = ROLES[mode]
        prompt = self.rt.prompts.render(
            role, self.rt.config.roundtable.prompts[role], items="\n".join(lines)
        )
        messages = tuple(
            Message(m.role, m.content, tuple(media)) if m.role == "user" else m
            for m in prompt.messages
        )
        params = {"max_tokens": cfg.max_tokens, "reasoning": {"effort": "low"}}
        completion = await self._call(room, model.id, messages, prompt, params)
        if completion is None:
            return {}
        found = parse_cards(completion.text, len(items))
        return {it.id: self._clip(found[n]) for n, it in enumerate(items, 1) if n in found}

    async def _call(
        self, room: str, model_id: str, messages: Any, prompt: Any, params: dict[str, Any]
    ) -> Completion | None:
        session = self.rt.repo.chat_session(room)
        try:
            completion = await self.rt.router.complete(model_id, messages, params)
        except (AllChannelsFailed, NoChannelAvailable, ProviderError) as exc:
            self.rt.repo.record_call(
                session,
                step=STEP,
                role="preprocess",
                model_id=model_id,
                messages=messages,
                prompt=prompt,
                failure=exc,
            )
            log.warning("感官调用失败")
            return None
        self.rt.repo.record_call(
            session,
            step=STEP,
            role="preprocess",
            model_id=model_id,
            messages=messages,
            prompt=prompt,
            completion=completion,
        )
        return completion


def media_block(who: str, cards: list[dict[str, Any]]) -> str:
    """聊天记录里给成员看的感知卡：放在 <media> 标签里（标签内的指令无效）。"""
    out = []
    for c in cards:
        label = LABELS.get(c.get("kind", ""), "文件")
        text = _neutralize(str(c.get("text", "")))
        out.append(f'<media from="{who}" kind="{label}">{text}</media>')
    return "\n".join(out)
