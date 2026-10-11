"""成员形象素材（头像、小人）：读取用户本机素材目录里的 members.json。

素材不进 git（data/ 已忽略）。目录或 members.json 不存在、损坏时返回 available=False 与原因，
绝不抛错。图片路径相对 members.json，必须落在素材目录内；文件不存在的图片记为 None。
成员按 vendor 对应到配置里的模型（不写死任何厂商或模型名）。
小人的逐帧素材（可选）在 pet.json：缺失或损坏时当作没有，不影响其他图片。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

MEMBERS_FILE = "members.json"
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
SIZES = ("256", "128", "64")
PET_FILE = "pet.json"
# 小人的动作与表情名（与 web/js/agent/brain.js 一致）；pet.json 里其他名字忽略
PET_ACTIONS = (
    "idle",
    "blink",
    "walk",
    "jump",
    "wave",
    "think",
    "talk",
    "type",
    "celebrate",
    "sleepy",
    "sleep",
    "drag",
    "land",
)
PET_EXPRESSIONS = (
    "happy",
    "smug",
    "thinking",
    "surprised",
    "confused",
    "shy",
    "angry",
    "sad",
    "sleepy",
)
PET_MAX_FRAMES = 64
PET_FPS = (1, 30)
PET_MAX_BYTES = 256 * 1024
# 只接受 #rgb / #rrggbb / #rrggbbaa，防止把任意文字写进页面样式
COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})")


class _Entry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    vendor: str = Field(min_length=1)
    nickname: str | None = None
    theme_color: str | None = None
    order: int = 0
    status: str | None = None
    images: dict[str, Any] = Field(default_factory=dict)


class _File(BaseModel):
    model_config = ConfigDict(extra="ignore")

    version: int | None = None
    members: list[_Entry]


@dataclass(frozen=True)
class Member:
    id: str
    name: str
    vendor: str
    nickname: str | None
    theme_color: str | None
    order: int
    status: str | None
    # 键同 members.json；值为素材目录内的相对路径（posix），尺寸图为 {"256": 路径或 None, …}
    images: dict[str, Any] = field(default_factory=dict)
    # 逐帧素材：{"fps", "actions": {名: {"frames": [相对路径], "fps", "loop"}}, "expressions"}
    pet: dict[str, Any] | None = None

    @property
    def display(self) -> str:
        return self.nickname or self.name


@dataclass(frozen=True)
class MembersArt:
    available: bool
    reason: str | None = None
    members: tuple[Member, ...] = ()

    def model_map(self, models: Iterable[tuple[str, str]]) -> dict[str, str]:
        """(模型 id, vendor) → {模型 id: 成员 id}。

        vendor 不区分大小写；同一 vendor 有多个成员时取排序靠前的。
        """
        by_vendor: dict[str, str] = {}
        for m in self.members:
            by_vendor.setdefault(m.vendor.strip().lower(), m.id)
        out = {}
        for model_id, vendor in models:
            member = by_vendor.get(vendor.strip().lower())
            if member:
                out[model_id] = member
        return out


def resolve_file(root: Path | None, rel: str, types: Iterable[str] = IMAGE_TYPES) -> Path | None:
    """素材目录内、扩展名允许、确实存在的图片文件；否则 None（防路径穿越）。"""
    if root is None or not isinstance(rel, str) or not rel or "\x00" in rel:
        return None
    if Path(rel).is_absolute() or rel.startswith(("/", "\\")):
        return None
    try:
        base = root.resolve()
        path = (base / rel).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not path.is_relative_to(base) or path.suffix.lower() not in types:
        return None
    return path if path.is_file() else None


def mime_of(path: Path) -> str:
    return IMAGE_TYPES[path.suffix.lower()]


def _image(root: Path, value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    path = resolve_file(root, value)
    return path.relative_to(root.resolve()).as_posix() if path else None


def _images(root: Path, raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            out[key] = {size: _image(root, value.get(size)) for size in SIZES}
        else:
            out[key] = _image(root, value)
    return out


def _fps(value: Any, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return int(min(max(value, PET_FPS[0]), PET_FPS[1]))


def _clips(root: Path, base: str, raw: Any, names: tuple[str, ...], fps: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for name in names:
        clip = raw.get(name)
        if not isinstance(clip, dict) or not isinstance(clip.get("frames"), list):
            continue
        frames = [
            f
            for f in (
                _image(root, f"{base}{x}") if isinstance(x, str) and x else None
                for x in clip["frames"][:PET_MAX_FRAMES]
            )
            if f
        ]
        if frames:
            out[name] = {
                "frames": frames,
                "fps": _fps(clip.get("fps"), fps),
                "loop": bool(clip.get("loop", name in ("idle", "walk", "sleep", "drag", "type"))),
            }
    return out


def _pet(root: Path, member_id: str, value: Any) -> dict[str, Any] | None:
    """读 pet.json；没有、越界、损坏或一帧都没有时返回 None。帧路径相对 pet.json 所在目录。"""
    rel = value if isinstance(value, str) and value else f"{member_id}/{PET_FILE}"
    path = resolve_file(root, rel, (".json",))
    if path is None:
        return None
    try:
        if path.stat().st_size > PET_MAX_BYTES:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    folder = path.parent.relative_to(root.resolve()).as_posix()
    base = "" if folder == "." else f"{folder}/"
    fps = _fps(data.get("fps"), 8)
    actions = _clips(root, base, data.get("actions"), PET_ACTIONS, fps)
    expressions = _clips(root, base, data.get("expressions"), PET_EXPRESSIONS, fps)
    if not actions and not expressions:
        return None
    return {"fps": fps, "actions": actions, "expressions": expressions}


def load_members(root: Path | None) -> MembersArt:
    if root is None:
        return MembersArt(False, "没有设置素材目录")
    try:
        if not root.is_dir():
            return MembersArt(False, "素材目录不存在")
        path = root / MEMBERS_FILE
        if not path.is_file():
            return MembersArt(False, f"素材目录里没有 {MEMBERS_FILE}")
        data = _File.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
        return MembersArt(False, f"{MEMBERS_FILE} 无法读取：{type(exc).__name__}")
    members, seen = [], set()
    for e in sorted(data.members, key=lambda e: (e.order, e.id)):
        if e.id in seen:
            continue
        seen.add(e.id)
        color = e.theme_color if e.theme_color and COLOR.fullmatch(e.theme_color) else None
        members.append(
            Member(
                id=e.id,
                name=e.name,
                vendor=e.vendor,
                nickname=e.nickname,
                theme_color=color,
                order=e.order,
                status=e.status,
                images=_images(root, {k: v for k, v in e.images.items() if k != "pet"}),
                pet=_pet(root, e.id, e.images.get("pet")),
            )
        )
    return MembersArt(True, None, tuple(members))
