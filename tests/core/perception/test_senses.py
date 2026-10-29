"""感官的本地处理：按时间取帧、拼宫格、抽声音、缓存（不调用模型）。"""

from __future__ import annotations

import io
import wave

import pytest

from roundtable.core.perception.senses import (
    PerceptionCache,
    SenseUnavailable,
    clock,
    extract_audio,
    grids,
    sample_frames,
    video_duration,
)

from .media import make_video


def test_frames_are_spread_over_time_and_grids_cut_images_by_four():
    data = make_video(4)
    assert video_duration(data) == pytest.approx(4, abs=0.2)
    frames = sample_frames(data, 8, 64)
    assert len(frames) == 8
    times = [f.at for f in frames]
    assert times == sorted(times) and times[0] < 0.6 and times[-1] > 3.4
    pictures = grids(frames)
    assert len(pictures) == 2  # 8 帧 → 2 张宫格图
    from PIL import Image

    with Image.open(io.BytesIO(pictures[0])) as im:
        assert im.size == (128, 96)  # 2×2 格，每格 64×48
    assert clock(65.4) == "1:05" and clock(0) == "0:00"


def test_extract_audio_is_16k_mono_wav_and_respects_the_limit():
    data = make_video(4)
    wav = extract_audio(data, 600)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
        assert w.getnframes() / 16000 == pytest.approx(4, abs=0.3)
    short = extract_audio(data, 1)
    with wave.open(io.BytesIO(short)) as w:
        assert w.getnframes() == 16000
    assert extract_audio(make_video(2, audio=False), 600) is None


def test_bad_video_raises_sense_unavailable():
    with pytest.raises(SenseUnavailable):
        sample_frames(b"not a video at all", 4, 64)


def test_cache_fine_satisfies_fast_and_transcripts_are_shared(tmp_path):
    sha = "a" * 64
    cache = PerceptionCache(tmp_path / "p")
    assert cache.card(sha, "fast") is None
    cache.put_card(sha, "fast", "快速的卡")
    assert cache.card(sha, "fast") == "快速的卡" and cache.card(sha, "fine") is None
    cache.put_card(sha, "fine", "精细的卡")
    assert cache.card(sha, "fast") == "精细的卡"  # 有精细的就用精细的
    cache.put_transcript(sha, "你好")
    # 重新打开（换一个实例）也能读到：存在本机文件里
    again = PerceptionCache(tmp_path / "p")
    assert again.card(sha, "fine") == "精细的卡" and again.transcript(sha) == "你好"
    assert again.card("../x", "fast") is None  # 键名只能是哈希
    assert again.clear() == 3 and again.card(sha, "fast") is None
    memory_only = PerceptionCache(None)
    memory_only.put_card(sha, "fast", "x")
    assert memory_only.card(sha, "fast") == "x" and memory_only.clear() == 0
