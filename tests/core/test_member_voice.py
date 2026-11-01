from __future__ import annotations

import asyncio
import io
import wave

import pytest

from roundtable.core.config import load_config
from roundtable.core.member_voice import (
    MAX_CHARS,
    MemberVoices,
    VoiceBudgetExceeded,
    VoiceUnavailable,
    audio_seconds,
)
from roundtable.core.providers import ErrorKind, FakeProvider
from roundtable.core.runtime import Runtime


def build(tmp_path=None, **media):
    config = load_config()
    policy = config.roundtable.request.model_copy(update={"backoff_s": 0, "failover_rounds": 1})
    rules = config.roundtable.media.model_copy(update=media)
    rt_cfg = config.roundtable.model_copy(update={"request": policy, "media": rules})
    config = config.model_copy(update={"roundtable": rt_cfg})
    fake = FakeProvider("openrouter")
    rt = Runtime.build(config=config, providers={"openrouter": fake}, db_path=":memory:")
    return rt, fake, MemberVoices(rt, tmp_path)


def speech_calls(fake):
    return [c for c in fake.media_calls if c[0] == "speech"]


def test_every_member_has_a_voice_in_each_tier():
    config = load_config()
    vendors = {m.vendor for m in config.models.models if m.seat}
    for tier in ("budget", "flagship"):
        tts = {
            m.id for m in config.models.models if "tts" in m.tags and m.enabled and m.tier == tier
        }
        for vendor in vendors:
            assert tts & set(config.personas.voices.get(vendor, {})), (vendor, tier)


def test_chinese_text_uses_a_zh_model_with_the_members_own_voice():
    rt, fake, voices = build()
    model, voice = voices.model_for("Anthropic", "你好呀", "flagship")
    assert "zh" in model.tags
    assert voice == rt.config.personas.voices["Anthropic"][model.id]
    clip = asyncio.run(voices.speak("Anthropic", "你好呀", tier="flagship"))
    [(_, route, text, params)] = speech_calls(fake)
    assert text == "你好呀" and params["voice"] == voice
    assert clip.voice == voice and not clip.cached and clip.data
    assert clip.mime.startswith("audio/")


def test_members_sound_different_and_skip_models_without_their_voice():
    rt, _, voices = build()
    budget = {voices.model_for(v, "你好")[1] for v in rt.config.personas.voices}
    assert len(budget) == len(rt.config.personas.voices)
    # 只有两个音色的中文模型没给成员配：中文也退回到配了音色的模型
    model, _ = voices.model_for("Anthropic", "你好")
    assert model.id in rt.config.personas.voices["Anthropic"]


def test_english_text_prefers_the_default_model():
    rt, _, voices = build()
    model, _ = voices.model_for("Anthropic", "hello there")
    assert model.default


def test_same_text_is_synthesized_once_and_cached_on_disk(tmp_path):
    rt, fake, voices = build(tmp_path)
    first = asyncio.run(voices.speak("Google", "今天  天气\n不错"))
    again = asyncio.run(voices.speak("Google", "今天 天气 不错"))
    assert len(speech_calls(fake)) == 1
    assert again.cached and again.cost_usd == 0 and again.data == first.data
    # 新建一个也能读到磁盘上的缓存
    fresh = MemberVoices(rt, tmp_path)
    assert asyncio.run(fresh.speak("Google", "今天 天气 不错")).cached
    assert len(speech_calls(fake)) == 1


def test_cost_is_recorded_as_media_on_a_hidden_session():
    rt, fake, voices = build()
    fake.media_cost = 0.01
    asyncio.run(voices.speak("xAI", "测试一下"))
    assert voices.month_spent() == pytest.approx(0.01)
    assert all(s["id"] != rt.repo.voice_session() for s in rt.repo.list_sessions())


def test_monthly_creative_cap_blocks_new_synthesis_but_not_cache():
    rt, fake, voices = build()
    fake.media_cost = 0.01
    asyncio.run(voices.speak("xAI", "第一句"))
    rt2_rules = rt.config.roundtable.media.model_copy(update={"monthly_usd": 0.01})
    rt.config = rt.config.model_copy(
        update={"roundtable": rt.config.roundtable.model_copy(update={"media": rt2_rules})}
    )
    with pytest.raises(VoiceBudgetExceeded):
        asyncio.run(voices.speak("xAI", "第二句"))
    assert asyncio.run(voices.speak("xAI", "第一句")).cached


def test_text_is_trimmed_and_empty_text_rejected():
    _, fake, voices = build()
    asyncio.run(voices.speak("DeepSeek", "啊" * (MAX_CHARS + 50)))
    assert len(speech_calls(fake)[0][2]) == MAX_CHARS
    with pytest.raises(ValueError):
        asyncio.run(voices.speak("DeepSeek", "   "))


def test_member_without_voices_or_media_off_is_unavailable():
    _, _, voices = build()
    assert voices.model_for("Nobody", "你好") is None
    with pytest.raises(VoiceUnavailable):
        asyncio.run(voices.speak("Nobody", "你好"))
    _, _, off = build(enabled=False)
    assert off.model_for("Anthropic", "你好") is None


def test_failed_synthesis_is_unavailable():
    _, fake, voices = build()
    fake.submit_errors.extend([ErrorKind.SERVER] * 20)
    with pytest.raises(VoiceUnavailable):
        asyncio.run(voices.speak("Alibaba", "你好"))


def test_audio_seconds_for_wav_and_mp3():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 16000)
    assert audio_seconds(buf.getvalue(), "audio/wav") == pytest.approx(2.0)
    # MPEG-1 Layer III，128 kbps：16000 字节 ≈ 1 秒
    frame = bytes([0xFF, 0xFB, 0x90, 0x00])
    data = b"ID3" + bytes([3, 0, 0, 0, 0, 0, 10]) + b"\0" * 10 + frame + b"\0" * 15996
    assert audio_seconds(data, "audio/mpeg") == pytest.approx(1.0)
    assert audio_seconds(b"junk", "audio/mpeg") is None
    assert audio_seconds(b"junk", "audio/ogg") is None


def test_repo_voice_table_only_names_speech_models():
    config = load_config()
    tts = {m.id for m in config.models.models if "tts" in m.tags}
    for vendor, voices in config.personas.voices.items():
        assert set(voices) <= tts, vendor


def build_local(tmp_path=None):
    """打开本地配音模型：local_tts 渠道是另一个假渠道。"""
    config = load_config()
    models = [
        m.model_copy(update={"enabled": True}) if m.id == "pc-tts" else m
        for m in config.models.models
    ]
    config = config.model_copy(
        update={"models": config.models.model_copy(update={"models": models})}
    )
    policy = config.roundtable.request.model_copy(update={"backoff_s": 0, "failover_rounds": 1})
    config = config.model_copy(
        update={"roundtable": config.roundtable.model_copy(update={"request": policy})}
    )
    fake, local = FakeProvider("openrouter"), FakeProvider("local_tts")
    rt = Runtime.build(
        config=config, providers={"openrouter": fake, "local_tts": local}, db_path=":memory:"
    )
    return rt, fake, local, MemberVoices(rt, tmp_path)


def test_local_tts_is_tried_first_with_the_members_preset_and_costs_nothing():
    rt, fake, local, voices = build_local()
    model, voice = voices.model_for("OpenAI", "你好", "budget")
    assert model.id == "pc-tts" and voice == "rt-openai"
    # 全力档要高质量：本地配音在节电档，不进全力档
    assert voices.model_for("OpenAI", "你好", "flagship")[0].id != "pc-tts"
    clip = asyncio.run(voices.speak("OpenAI", "你好呀"))
    assert clip.model_id == "pc-tts" and clip.cost_usd == 0
    [(_, route, text, params)] = speech_calls(local)
    assert route == "preset-voice" and params["voice"] == "rt-openai" and params["path"] == "tts"
    assert speech_calls(fake) == []


def test_local_tts_down_falls_back_to_the_next_voice_model():
    rt, fake, local, voices = build_local()
    local.submit_errors.extend([ErrorKind.NETWORK] * 5)
    clip = asyncio.run(voices.speak("OpenAI", "你好呀"))
    assert clip.model_id != "pc-tts" and clip.voice in rt.config.personas.voices["OpenAI"].values()
    assert speech_calls(local) and speech_calls(fake)
    # 再念同一句：命中缓存，不再去试本地服务
    again = asyncio.run(voices.speak("OpenAI", "你好呀"))
    assert again.cached and len(speech_calls(local)) == len(local.media_calls)
