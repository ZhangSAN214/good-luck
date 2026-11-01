"""成员固定音色：用某位成员自己的合成音色把一段文字念出来。

- 每位成员（键为厂商，和聊天成员 id 相同）在 personas.yaml 的 `voices` 里按语音模型各写一个音色名；
  只用语音模型自带的合成音色，不克隆真人声音。
- 选模型只看能力标签（tts）、档位、这位成员有没有配这个模型的音色、脚本是不是中文（偏好 zh 标签）
  和 default，不看模型名；同一位成员、同一种语言总是同一个模型和音色。
- 花费计入创作输出的每月上限（media.monthly_usd，calls.role = media），调用挂在隐藏会话
  workflow = 'voice' 上，同时进入每月 / 每日总预算。
- 同一个模型、同一个音色、同一段文字只合成一次：结果缓存在数据库旁边的 voice_cache/
  （内存数据库时缓存在内存里），再次请求不花钱。

聊天里的语音消息、「听 TA 说」由聊天那边接（`MemberVoices.speak` 返回音频字节、类型和时长）；
成员设定页的「试听音色」也用它。
"""

from __future__ import annotations

import hashlib
import io
import json
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from roundtable.core.budget.guard import month_window
from roundtable.core.config import ModelSpec
from roundtable.core.media.pricing import mostly_cjk, speech_cost
from roundtable.core.media.select import media_group
from roundtable.core.providers import (
    AllChannelsFailed,
    Completion,
    Message,
    NoChannelAvailable,
)

MAX_CHARS = 500  # 一次最多念这么多字（聊天里的一条语音、一句试听）
SAMPLE_TEXT = "你好呀，我是{name}。今天也一起加油吧。"  # 成员设定页「试听音色」念的话
EPS = 1e-9


class VoiceUnavailable(Exception):
    """这位成员没有可用的音色（没配、没有可用的语音模型，或合成失败）。"""


class VoiceBudgetExceeded(Exception):
    """再合成就会超过创作输出的每月上限。"""


@dataclass(frozen=True)
class VoiceClip:
    data: bytes
    mime: str
    seconds: float | None  # 估算的时长；算不出来时为 None
    cost_usd: float  # 这次实际花的钱（命中缓存为 0）
    model_id: str
    voice: str
    cached: bool


class _Cache:
    """按 (模型, 音色, 文字) 的哈希存音频：目录为空时存在内存里。"""

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._memory: dict[str, tuple[bytes, dict[str, Any]]] = {}

    def get(self, key: str) -> tuple[bytes, dict[str, Any]] | None:
        if self.root is None:
            return self._memory.get(key)
        meta_path = self.root / f"{key}.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return (self.root / f"{key}.audio").read_bytes(), meta
        except (OSError, ValueError):
            return None

    def put(self, key: str, data: bytes, meta: dict[str, Any]) -> None:
        if self.root is None:
            self._memory[key] = (data, meta)
            return
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{key}.audio").write_bytes(data)
        (self.root / f"{key}.json").write_text(json.dumps(meta), encoding="utf-8")


class MemberVoices:
    def __init__(self, rt: Any, cache_dir: Path | None = None) -> None:
        self.rt = rt
        self._cache = _Cache(cache_dir)

    @property
    def rules(self) -> Any:
        return self.rt.config.roundtable.media

    def voices_of(self, member: str) -> dict[str, str]:
        """这位成员配置的音色：语音模型 id → 音色名。"""
        return dict(self.rt.config.personas.voices.get(member, {}))

    def model_for(
        self, member: str, text: str = "", tier: str | None = None
    ) -> tuple[ModelSpec, str] | None:
        """这位成员这次先用的语音模型和音色；没有可用的返回 None。"""
        picks = self.candidates(member, text, tier)
        return picks[0] if picks else None

    def candidates(
        self, member: str, text: str = "", tier: str | None = None
    ) -> list[tuple[ModelSpec, str]]:
        """按先后排好的 (语音模型, 音色)：不花钱的（如本地配音）先试，其次中文偏好 zh 标签，
        再其次 default；前一个合成失败时换下一个。"""
        if not self.rules.enabled:
            return []
        mine = self.voices_of(member)
        group = [
            m
            for m in media_group(self.rt.router, "speech", tier or self.rules.default_tier)
            if m.id in mine
        ]
        cjk = mostly_cjk(text)
        group.sort(
            key=lambda m: (
                speech_cost(m, None, 100) > 0,
                cjk and "zh" not in m.tags,
                not m.default,
            )
        )
        return [(m, mine[m.id]) for m in group]

    def month_spent(self) -> float:
        start, end = month_window(datetime.fromisoformat(self.rt.repo.clock()))
        return self.rt.repo.spent_in_role("media", start, end)

    async def speak(self, member: str, text: str, *, tier: str | None = None) -> VoiceClip:
        """用这位成员的音色念 text。命中缓存不花钱；超出创作月上限时抛 VoiceBudgetExceeded。"""
        text = " ".join(text.split())[:MAX_CHARS]
        if not text:
            raise ValueError("要念的文字是空的")
        picks = self.candidates(member, text, tier)
        if not picks:
            raise VoiceUnavailable("这位成员没有可用的音色（没有配置，或没有可用的语音模型）")
        for model, voice in picks:
            key = hashlib.sha256(f"{model.id}\n{voice}\n{text}".encode()).hexdigest()
            hit = self._cache.get(key)
            if hit is not None:
                data, meta = hit
                return VoiceClip(
                    data, meta["mime"], meta.get("seconds"), 0.0, model.id, voice, True
                )
        for model, voice in picks:
            try:
                return await self._synthesize(model, voice, text)
            except VoiceUnavailable:
                continue  # 换下一个语音模型（如本地配音服务没开）
        raise VoiceUnavailable("合成失败（所有语音模型都不可用）")

    async def _synthesize(self, model: ModelSpec, voice: str, text: str) -> VoiceClip:
        key = hashlib.sha256(f"{model.id}\n{voice}\n{text}".encode()).hexdigest()
        estimate = speech_cost(model, None, len(text))
        limit = self.rules.monthly_usd
        if limit > 0 and self.month_spent() + estimate > limit + EPS:
            raise VoiceBudgetExceeded(f"本月创作输出快超过上限 ${limit:g}，这次先不念了")
        params = {"response_format": self.rules.speech.format}
        try:
            inv = await self.rt.router.invoke(
                model.id,
                lambda prov, route, m: prov.synthesize_speech(
                    route.model, text, {**params, **m.params_for(route), "voice": voice}
                ),
            )
        except (AllChannelsFailed, NoChannelAvailable):
            raise VoiceUnavailable("合成失败（所有渠道都不可用）") from None
        out = inv.result
        if out.cost_usd is not None:
            cost, source = out.cost_usd, "reported"
        else:
            cost, source = speech_cost(model, inv.route, len(text)), "estimated"
        self._record(model, text, inv, cost, source)
        seconds = audio_seconds(out.media.data, out.media.mime)
        self._cache.put(key, out.media.data, {"mime": out.media.mime, "seconds": seconds})
        return VoiceClip(out.media.data, out.media.mime, seconds, cost, model.id, voice, False)

    def _record(self, model: ModelSpec, text: str, inv: Any, cost: float, source: str) -> None:
        completion = Completion(
            text="",
            model_id=model.id,
            channel=inv.channel,
            channel_kind=inv.channel_kind,
            route_model=inv.route.model,
            input_tokens=0,
            output_tokens=0,
            cached_tokens=0,
            cost_usd=cost,
            cost_source=source,  # type: ignore[arg-type]
            latency_s=getattr(inv, "latency_s", 0.0),
            attempts=getattr(inv, "attempts", ()),
        )
        self.rt.repo.record_call(
            self.rt.repo.voice_session(),
            step="member_voice",
            role="media",
            model_id=model.id,
            messages=[Message("user", text)],
            completion=completion,
        )


# --- 时长 ------------------------------------------------------------------------

# MPEG-1 Layer III 的码率表（kbps），按帧头里的码率索引
_MP3_KBPS = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
# MPEG-2 / 2.5 Layer III
_MP3_KBPS_LOW = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)


def audio_seconds(data: bytes, mime: str) -> float | None:
    """音频时长（估算）：wav 按帧数；mp3 按第一帧的码率（固定码率准，可变码率是近似）。"""
    if mime in ("audio/wav", "audio/x-wav"):
        try:
            with wave.open(io.BytesIO(data)) as w:
                return w.getnframes() / float(w.getframerate())
        except (wave.Error, EOFError, ZeroDivisionError):
            return None
    if mime == "audio/mpeg":
        start = 0
        if data[:3] == b"ID3" and len(data) >= 10:
            size = 0
            for b in data[6:10]:
                size = (size << 7) | (b & 0x7F)
            start = 10 + size
        for i in range(start, min(len(data) - 3, start + 4096)):
            if data[i] == 0xFF and data[i + 1] & 0xE0 == 0xE0:
                version = (data[i + 1] >> 3) & 0x03  # 3 = MPEG-1
                index = data[i + 2] >> 4
                table = _MP3_KBPS if version == 3 else _MP3_KBPS_LOW
                if 0 < index < len(table):
                    return round((len(data) - i) * 8 / (table[index] * 1000), 2)
        return None
    return None
