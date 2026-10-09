"""视频截帧：用 PyAV 生成一个很短的真实视频，再按时间均匀取帧。"""

from __future__ import annotations

import io

import pytest

from roundtable.core.media import FramesUnavailable, extract_frames

av = pytest.importorskip("av")
Image = pytest.importorskip("PIL.Image")


def make_video(frames: int = 12) -> bytes:
    buf = io.BytesIO()
    with av.open(buf, "w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        for i in range(frames):
            image = Image.new("RGB", (64, 48), (i * 20 % 256, 80, 160))
            frame = av.VideoFrame.from_image(image)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buf.getvalue()


def test_extracts_evenly_spaced_jpeg_frames():
    frames = extract_frames(make_video(), 4)
    assert len(frames) == 4
    assert all(f.kind == "image" and f.mime == "image/jpeg" for f in frames)
    assert all(f.data[:2] == b"\xff\xd8" for f in frames)  # JPEG 文件头
    assert len({f.data for f in frames}) > 1  # 不是同一帧
    assert [f.name for f in frames] == ["frame1.jpg", "frame2.jpg", "frame3.jpg", "frame4.jpg"]


def test_fewer_frames_than_requested():
    assert len(extract_frames(make_video(2), 6)) == 2


def test_garbage_is_reported():
    with pytest.raises(FramesUnavailable):
        extract_frames(b"not a video", 4)
