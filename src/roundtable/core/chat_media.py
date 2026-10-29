"""聊天与朋友圈里用户发的图片、视频、语音：只存在本机（数据库旁边的 chat_media/），不进 git。

文件名是内容的哈希加扩展名，同一个文件只存一份。只认常见的图片 / 视频 / 音频格式，
并且检查文件开头的特征字节，扩展名和声明的类型对不上就拒收。
消息文字里是「[图片]」「[语音 5 秒]」这样的占位；感官（core/perception）把文件整理成感知卡后，
成员读的是卡（文件本身只发给看图 / 转写模型，见 docs/PERCEPTION.md）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

# MIME → (种类, 扩展名)
TYPES: dict[str, tuple[str, str]] = {
    "image/png": ("image", "png"),
    "image/jpeg": ("image", "jpg"),
    "image/gif": ("image", "gif"),
    "image/webp": ("image", "webp"),
    "video/mp4": ("video", "mp4"),
    "video/webm": ("video", "webm"),
    "video/quicktime": ("video", "mov"),
    "audio/webm": ("voice", "weba"),
    "audio/ogg": ("voice", "ogg"),
    "audio/mpeg": ("voice", "mp3"),
    "audio/mp4": ("voice", "m4a"),
    "audio/wav": ("voice", "wav"),
}
MIME_OF = {ext: mime for mime, (_, ext) in TYPES.items()}
LIMITS = {"image": 10 * 1024 * 1024, "video": 60 * 1024 * 1024, "voice": 8 * 1024 * 1024}
MAX_BYTES = max(LIMITS.values())
MAX_ITEMS = 9  # 一条消息 / 动态最多带几个
LABELS = {"image": "图片", "video": "视频", "voice": "语音"}
_NAME = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,4}$")


class MediaError(ValueError):
    pass


def _looks_like(kind: str, ext: str, head: bytes) -> bool:
    """文件开头的特征字节是否和声明的类型一致（够用就好，不做完整解析）。"""
    if ext == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "jpg":
        return head.startswith(b"\xff\xd8\xff")
    if ext == "gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if ext == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext == "wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext in {"webm", "weba"}:
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if ext == "ogg":
        return head.startswith(b"OggS")
    if ext == "mp3":
        return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] >> 5 == 7)
    if ext in {"mp4", "m4a", "mov"}:
        return head[4:8] == b"ftyp"
    return False


@dataclass(frozen=True)
class Saved:
    id: str
    kind: str
    mime: str
    size: int


class ChatMediaStore:
    """root 为空（内存数据库）时不能存。"""

    def __init__(self, root: Path | None) -> None:
        self.root = root

    def save(self, data: bytes, mime: str) -> Saved:
        if self.root is None:
            raise MediaError("当前没有本地数据目录，不能保存文件")
        mime = (mime or "").split(";", 1)[0].strip().lower()
        if mime not in TYPES:
            raise MediaError("只支持图片（png / jpg / gif / webp）、视频（mp4 / webm / mov）和语音")
        kind, ext = TYPES[mime]
        if not data:
            raise MediaError("文件是空的")
        if len(data) > LIMITS[kind]:
            raise MediaError(f"{LABELS[kind]}太大（最多 {LIMITS[kind] // 1024 // 1024} MB）")
        if not _looks_like(kind, ext, data[:16]):
            raise MediaError(f"文件内容不像{LABELS[kind]}")
        name = f"{hashlib.sha256(data).hexdigest()[:32]}.{ext}"
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        if not path.exists():
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
        return Saved(name, kind, mime, len(data))

    def load(self, name: str) -> tuple[bytes, str] | None:
        if self.root is None or not _NAME.match(name or ""):
            return None
        mime = MIME_OF.get(name.rsplit(".", 1)[1])
        path = self.root / name
        if mime is None or not path.is_file():
            return None
        return path.read_bytes(), mime

    def kind_of(self, name: str) -> str | None:
        if not _NAME.match(name or ""):
            return None
        mime = MIME_OF.get(name.rsplit(".", 1)[1])
        if mime is None or self.root is None or not (self.root / name).is_file():
            return None
        return TYPES[mime][0]


def clean_items(store: ChatMediaStore, items: list[dict] | None) -> list[dict]:
    """检查前端带来的附件清单：只留本机确实有的文件，规范字段。"""
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            raise MediaError("附件格式不对")
        name = str(it.get("id") or "")
        kind = store.kind_of(name)
        if kind is None:
            raise MediaError("附件不存在，请重新选择")
        row: dict = {"id": name, "kind": kind}
        for key in ("duration", "w", "h"):
            val = it.get(key)
            if isinstance(val, int | float) and 0 < val < 100_000:
                row[key] = round(float(val), 1)
        out.append(row)
    if len(out) > MAX_ITEMS:
        raise MediaError(f"一次最多 {MAX_ITEMS} 个")
    return out


def placeholder(items: list[dict]) -> str:
    """消息里的文字占位（感官整理出感知卡后，成员另外读到卡）。"""
    parts = []
    for it in items:
        if it["kind"] == "voice" and it.get("duration"):
            parts.append(f"[语音 {max(1, round(it['duration']))} 秒]")
        else:
            parts.append(f"[{LABELS[it['kind']]}]")
    return " ".join(parts)
