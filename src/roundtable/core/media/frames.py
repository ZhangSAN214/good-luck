"""视频截帧：评审者需要真正看到视频内容，所以按时间均匀取几帧作为图片发给带 vision 标签的模型。"""

from __future__ import annotations

import io

from roundtable.core.providers import Media


class FramesUnavailable(RuntimeError):
    """没有安装截帧所需的库（pip install 'roundtable[media]'），或视频无法解码。"""


def extract_frames(data: bytes, count: int, *, max_side: int = 768) -> list[Media]:
    try:
        import av  # type: ignore[import-not-found]
    except ImportError:
        raise FramesUnavailable("未安装 av（pip install 'roundtable[media]'）") from None
    try:
        with av.open(io.BytesIO(data)) as container:
            stream = next(s for s in container.streams if s.type == "video")
            frames = [f.to_image() for f in container.decode(stream)]
    except (StopIteration, OSError, ValueError, av.error.FFmpegError) as exc:
        raise FramesUnavailable(f"无法解码视频：{type(exc).__name__}") from None
    if not frames:
        raise FramesUnavailable("视频中没有画面")
    n = min(count, len(frames))
    picks = [
        frames[round(i * (len(frames) - 1) / (n - 1))] if n > 1 else frames[0] for i in range(n)
    ]
    out = []
    for i, image in enumerate(picks, 1):
        image = image.convert("RGB")
        image.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=80)
        out.append(Media("image", "image/jpeg", buf.getvalue(), f"frame{i}.jpg"))
    return out
