"""感官的本地处理（不调用模型、不花钱）：视频按时间取帧、拼宫格图、抽出声音；感知卡缓存。

视频解码用可选依赖 PyAV（``pip install 'roundtable[media]'``），拼图用 Pillow；没有安装或解码失败时
抛 ``SenseUnavailable``，调用方改用能拿到的部分（例如只有声音转写）。

思路借鉴 dsh-visual-plugin（MIT）：先核对文件、再按时间取关键帧、带时间戳按顺序交给看图模型；
dsh-vision-router（MIT）：按内容哈希缓存看过的结果。代码为圆桌重写。
"""

from __future__ import annotations

import io
import json
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SAMPLE_RATE = 16000  # 交给转写模型的声音：16 kHz 单声道 16 位
_KEY = re.compile(r"^[0-9a-f]{64}$")
MODES = ("fast", "fine")


class SenseUnavailable(RuntimeError):
    """没有安装 av / Pillow，或文件无法解码。"""


@dataclass(frozen=True)
class Frame:
    at: float  # 第几秒
    jpeg: bytes


def _av() -> Any:
    try:
        import av  # type: ignore[import-not-found]
    except ImportError:
        raise SenseUnavailable("未安装 av（pip install 'roundtable[media]'）") from None
    return av


def _thumb_jpeg(image: Any, side: int) -> bytes:
    image = image.convert("RGB")
    image.thumbnail((side, side))
    buf = io.BytesIO()
    image.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def video_duration(data: bytes) -> float | None:
    av = _av()
    try:
        with av.open(io.BytesIO(data)) as container:
            if container.duration:
                return container.duration / 1_000_000
            stream = next(s for s in container.streams if s.type == "video")
            if stream.duration and stream.time_base:
                return float(stream.duration * stream.time_base)
    except (StopIteration, OSError, ValueError, av.error.FFmpegError):
        return None
    return None


def sample_frames(data: bytes, count: int, side: int) -> list[Frame]:
    """按时间均匀取 count 帧（每段的中点），缩到长边不超过 side 像素。

    用跳转而不是逐帧解码，长视频也只解码取到的那几帧附近。不知道时长时退回逐帧解码、均匀挑选。
    """
    av = _av()
    duration = video_duration(data)
    frames: list[Frame] = []
    try:
        with av.open(io.BytesIO(data)) as container:
            stream = next(s for s in container.streams if s.type == "video")
            if duration and duration > 0 and stream.time_base:
                for i in range(count):
                    t = (i + 0.5) * duration / count
                    container.seek(int(t / stream.time_base), stream=stream, backward=True)
                    for frame in container.decode(stream):
                        at = float(frame.time) if frame.time is not None else t
                        if at + 0.05 >= t or at >= duration:
                            frames.append(Frame(round(at, 1), _thumb_jpeg(frame.to_image(), side)))
                            break
            else:
                decoded = container.decode(stream)
                every = [f for _, f in zip(range(count * 50), decoded, strict=False)]
                n = min(count, len(every))
                for i in range(n):
                    f = every[round(i * (len(every) - 1) / (n - 1))] if n > 1 else every[0]
                    at = float(f.time) if f.time is not None else float(i)
                    frames.append(Frame(round(at, 1), _thumb_jpeg(f.to_image(), side)))
    except (StopIteration, OSError, ValueError, av.error.FFmpegError) as exc:
        raise SenseUnavailable(f"无法解码视频：{type(exc).__name__}") from None
    if not frames:
        raise SenseUnavailable("视频中没有画面")
    return frames


def clock(seconds: float) -> str:
    s = max(0, round(seconds))
    return f"{s // 60}:{s % 60:02d}"


def grids(frames: list[Frame]) -> list[bytes]:
    """每 4 帧拼成一张 2×2 宫格图（按时间从左到右、从上到下），每格左上角标时间。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        raise SenseUnavailable("未安装 Pillow（pip install 'roundtable[media]'）") from None
    out = []
    for start in range(0, len(frames), 4):
        group = frames[start : start + 4]
        images = [Image.open(io.BytesIO(f.jpeg)).convert("RGB") for f in group]
        cw = max(im.width for im in images)
        ch = max(im.height for im in images)
        canvas = Image.new("RGB", (cw * 2, ch * 2), "black")
        draw = ImageDraw.Draw(canvas)
        for k, (im, f) in enumerate(zip(images, group, strict=True)):
            x, y = (k % 2) * cw, (k // 2) * ch
            canvas.paste(im, (x, y))
            label = clock(f.at)
            draw.rectangle((x, y, x + 8 + 7 * len(label), y + 16), fill="black")
            draw.text((x + 4, y + 2), label, fill="white")
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=80)
        out.append(buf.getvalue())
    return out


def extract_audio(data: bytes, max_seconds: int) -> bytes | None:
    """把视频里的声音取出来，转成 16 kHz 单声道 WAV（最多 max_seconds 秒）。没有声音轨返回 None。"""
    av = _av()
    pcm = bytearray()
    limit = max_seconds * SAMPLE_RATE * 2
    try:
        with av.open(io.BytesIO(data)) as container:
            streams = [s for s in container.streams if s.type == "audio"]
            if not streams:
                return None
            resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)

            def take(frames: Any) -> None:
                for f in frames:
                    pcm.extend(bytes(f.planes[0])[: f.samples * 2])

            for frame in container.decode(streams[0]):
                take(resampler.resample(frame))
                if len(pcm) >= limit:
                    break
            take(resampler.resample(None))
    except (OSError, ValueError, av.error.FFmpegError) as exc:
        raise SenseUnavailable(f"无法解码声音：{type(exc).__name__}") from None
    if not pcm:
        return None
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(pcm[:limit]))
    return buf.getvalue()


class PerceptionCache:
    """按文件内容哈希缓存感知卡和转写稿（JSON 文件，只存本机 data/perception/，不进 git）。

    精细的卡也能满足快速的请求；转写稿两种模式共用。root 为空（内存数据库）时只缓存在内存里。
    """

    def __init__(self, root: Path | None) -> None:
        self.root = root
        self._memory: dict[str, dict[str, Any]] = {}

    def _read(self, name: str) -> dict[str, Any] | None:
        if name in self._memory:
            return self._memory[name]
        if self.root is None:
            return None
        path = self.root / name
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _write(self, name: str, data: dict[str, Any]) -> None:
        self._memory[name] = data
        if self.root is None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def card(self, sha: str, mode: str) -> str | None:
        if not _KEY.match(sha):
            return None
        wanted = MODES[MODES.index(mode) :]  # 要快速时精细的也行
        for m in reversed(wanted):
            found = self._read(f"{sha}.{m}.json")
            if found and isinstance(found.get("text"), str):
                return found["text"]
        return None

    def put_card(self, sha: str, mode: str, text: str) -> None:
        if _KEY.match(sha) and mode in MODES:
            self._write(f"{sha}.{mode}.json", {"text": text})

    def transcript(self, sha: str) -> str | None:
        found = self._read(f"{sha}.transcript.json") if _KEY.match(sha) else None
        return found["text"] if found and isinstance(found.get("text"), str) else None

    def put_transcript(self, sha: str, text: str) -> None:
        if _KEY.match(sha):
            self._write(f"{sha}.transcript.json", {"text": text})

    def clear(self) -> int:
        """一键清空感官缓存，返回删掉的文件数。"""
        self._memory.clear()
        if self.root is None or not self.root.is_dir():
            return 0
        n = 0
        for path in self.root.glob("*.json"):
            path.unlink(missing_ok=True)
            n += 1
        return n
