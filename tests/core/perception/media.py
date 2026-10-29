"""测试用的小视频（几秒、64×48、可带 440 Hz 的声音），用 PyAV 现场编码；没装 av / numpy 时跳过。"""

from __future__ import annotations

import io

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 40


def make_video(seconds: int = 4, *, audio: bool = True) -> bytes:
    av = pytest.importorskip("av")
    np = pytest.importorskip("numpy")
    buf = io.BytesIO()
    with av.open(buf, "w", format="mp4") as c:
        video = c.add_stream("mpeg4", rate=10)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        sound = c.add_stream("aac", rate=16000) if audio else None
        if sound is not None:
            sound.layout = "mono"
        for i in range(seconds * 10):
            img = np.full((48, 64, 3), (i * 6) % 255, dtype=np.uint8)
            for p in video.encode(av.VideoFrame.from_ndarray(img, format="rgb24")):
                c.mux(p)
        for p in video.encode():
            c.mux(p)
        if sound is not None:
            t, n = 0, 1024
            while t < seconds * 16000:
                wave = np.sin(np.arange(t, t + n) / 16000 * 2 * np.pi * 440) * 8000
                frame = av.AudioFrame.from_ndarray(
                    wave.astype(np.int16).reshape(1, -1), format="s16", layout="mono"
                )
                frame.sample_rate, frame.pts = 16000, t
                for p in sound.encode(frame):
                    c.mux(p)
                t += n
            for p in sound.encode():
                c.mux(p)
    return buf.getvalue()
