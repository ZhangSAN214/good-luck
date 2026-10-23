"""视频截帧与图片缩小：评审者需要真正看到视频内容，所以按时间均匀取几帧作为图片发给带 vision
标签的模型；生成的大图转交前缩小（上传更快、看图 token 更少）。"""

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


def shrink_image(data: bytes, mime: str, max_side: int) -> tuple[bytes, str]:
    """把图片缩小到长边不超过 max_side 像素；已经够小、没有 Pillow 或无法解码时原样返回。

    有透明通道的图（如 PNG 海报）保持 PNG，其余转成 JPEG（质量 85）。
    """
    if max_side <= 0:
        return data, mime
    try:
        from PIL import Image
    except ImportError:
        return data, mime
    try:
        with Image.open(io.BytesIO(data)) as image:
            if max(image.size) <= max_side:
                return data, mime
            image.load()
            alpha = image.mode in ("RGBA", "LA") or "transparency" in image.info
            image.thumbnail((max_side, max_side))
            buf = io.BytesIO()
            if alpha:
                image.save(buf, "PNG", optimize=True)
                out = buf.getvalue(), "image/png"
            else:
                image.convert("RGB").save(buf, "JPEG", quality=85)
                out = buf.getvalue(), "image/jpeg"
    except (OSError, ValueError, Image.DecompressionBombError):
        return data, mime
    return out if len(out[0]) < len(data) else (data, mime)
