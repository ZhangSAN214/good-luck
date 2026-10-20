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


def test_shrink_image_keeps_small_images_and_shrinks_big_ones():
    import io

    from PIL import Image

    from roundtable.core.media import shrink_image

    def png(size, mode="RGB"):
        buf = io.BytesIO()
        Image.effect_noise(size, 64).convert(mode).save(buf, "PNG")
        return buf.getvalue()

    small = png((300, 200))
    assert shrink_image(small, "image/png", 1280) == (small, "image/png")
    big = png((2400, 1600))
    data, mime = shrink_image(big, "image/png", 1280)
    assert mime == "image/jpeg" and len(data) < len(big)
    assert max(Image.open(io.BytesIO(data)).size) == 1280
    clear = png((2000, 2000), "RGBA")  # 透明背景保持 PNG
    data, mime = shrink_image(clear, "image/png", 1000)
    assert mime == "image/png" and max(Image.open(io.BytesIO(data)).size) == 1000
    assert shrink_image(big, "image/png", 0) == (big, "image/png")  # 0 = 原图
    assert shrink_image(b"not an image", "image/png", 100) == (b"not an image", "image/png")
