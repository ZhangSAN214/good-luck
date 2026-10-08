"""识别上传文件的类型：扩展名与文件头必须一致（伪造扩展名的文件被拒绝）。"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from typing import Literal

Kind = Literal["image", "pdf", "docx", "text", "audio"]


class UploadError(ValueError):
    """上传的文件不被接受（类型、大小、内容等），信息可以直接展示给用户。"""


@dataclass(frozen=True)
class FileType:
    kind: Kind
    ext: str
    mime: str


# 扩展名 → (类别, MIME)
TYPES: dict[str, tuple[Kind, str]] = {
    "png": ("image", "image/png"),
    "jpg": ("image", "image/jpeg"),
    "jpeg": ("image", "image/jpeg"),
    "webp": ("image", "image/webp"),
    "gif": ("image", "image/gif"),
    "pdf": ("pdf", "application/pdf"),
    "docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "txt": ("text", "text/plain"),
    "md": ("text", "text/markdown"),
    "csv": ("text", "text/csv"),
    "mp3": ("audio", "audio/mpeg"),
    "wav": ("audio", "audio/wav"),
}

UNSUPPORTED_HINTS = {
    "doc": "旧版 Word（.doc）不支持，请另存为 .docx",
    "heic": "HEIC 图片不支持，请转成 JPG 或 PNG",
}


def _is_mp3(head: bytes) -> bool:
    return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0)


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            return "word/document.xml" in z.namelist()
    except (zipfile.BadZipFile, ValueError):
        return False


def _is_text(data: bytes) -> bool:
    sample = data[:8192]
    # 含 NUL 的是二进制文件（UTF-16 有 BOM 时允许）
    return b"\x00" not in sample or sample.startswith((b"\xff\xfe", b"\xfe\xff"))


def _matches(ext: str, data: bytes) -> bool:
    head = data[:16]
    checks = {
        "png": lambda: head.startswith(b"\x89PNG\r\n\x1a\n"),
        "jpg": lambda: head.startswith(b"\xff\xd8\xff"),
        "jpeg": lambda: head.startswith(b"\xff\xd8\xff"),
        "gif": lambda: head.startswith((b"GIF87a", b"GIF89a")),
        "webp": lambda: head[:4] == b"RIFF" and head[8:12] == b"WEBP",
        "pdf": lambda: data[:1024].lstrip().startswith(b"%PDF-"),
        "docx": lambda: head.startswith(b"PK\x03\x04") and _is_docx(data),
        "mp3": lambda: _is_mp3(head),
        "wav": lambda: head[:4] == b"RIFF" and head[8:12] == b"WAVE",
    }
    return checks.get(ext, lambda: _is_text(data))()


def detect(name: str, data: bytes) -> FileType:
    ext = PurePath(name).suffix.lower().lstrip(".")
    if not data:
        raise UploadError(f"文件「{name}」是空的")
    if ext in UNSUPPORTED_HINTS:
        raise UploadError(UNSUPPORTED_HINTS[ext])
    if ext not in TYPES:
        allowed = "、".join(sorted(TYPES))
        raise UploadError(f"不支持的文件类型「.{ext or '?'}」，支持：{allowed}")
    if not _matches(ext, data):
        raise UploadError(f"文件「{name}」的内容与扩展名 .{ext} 不符")
    kind, mime = TYPES[ext]
    return FileType(kind, ext, mime)


def display_name(name: str) -> str:
    """文件名只用于显示：去掉路径和控制字符，限制长度（从不用于拼接存储路径）。"""
    base = PurePath(name.replace("\\", "/")).name
    clean = "".join(ch for ch in base if ch.isprintable() and ch not in '<>"').strip()
    return (clean or "未命名")[:120]
