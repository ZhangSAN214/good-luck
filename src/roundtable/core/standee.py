"""终端首页的大立绘（伪 Live2D）：读取用户本机的 standee.json。

立绘和表情差分是用户本机素材，不进 git（data/ 已忽略）。默认清单是 data/avatars/standee.json，
图片路径相对清单所在目录，必须落在该目录内（防路径穿越）。清单不存在、损坏、图片缺失时
返回 available=False 与原因，绝不抛错；单个表情差分缺失时当作没有（前端用动作代替）。

清单示例（字段多余会报错，方便发现写错的名字）：

    {
      "name": "Claude",
      "persona": "<personas.yaml 里的键，可选：口头禅也会加进点击台词>",
      "variants": [
        {"id": "regular", "label": "常规", "image": "standee/regular.png",
         "expressions": {
           "blink": {"image": "standee/regular_blink.png"},
           "smile": {"image": "standee/smile_face.png", "box": [0.38, 0.08, 0.2, 0.13]}
         },
         "head": [0.47, 0.2]}
      ],
      "lines": {"greet": ["欢迎回来。"], "tap": ["……嗯？"], "head": ["别闹。"],
                "idle": ["今天读到一句好话。"]}
    }

表情差分可以是整张同尺寸图（只改了脸），也可以是一小块脸部贴片（box 为贴片在原图中的
左、上、宽、高，取值 0～1 的比例）。前端把贴片叠在原图上，换表情时淡入淡出。

一套立绘还可以写 rig（scripts/standee_rig.py 生成的 rig.json 原样抄进来）：瞳孔层和全身形变权重图，
前端支持 WebGL2 时用它做全身网格形变；rig 里的图缺了就当没有 rig，照旧用整张图。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from roundtable.core.members_art import mime_of, resolve_file

STANDEE_FILE = "standee.json"
# 前端认识的表情（与 web/js/standee.js 一致）；默认表情就是立绘本身
EXPRESSIONS = ("smile", "think", "surprise", "blink", "talk")
LINE_KINDS = ("greet", "tap", "head", "idle", "morning", "night")
MAX_BYTES = 64 * 1024
MAX_VARIANTS = 6
MAX_LINES = 60
MAX_LINE_CHARS = 80


class _Expression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(min_length=1)
    box: tuple[float, float, float, float] | None = None

    @field_validator("box")
    @classmethod
    def _box(cls, v: tuple[float, float, float, float] | None):
        if v is None:
            return v
        x, y, w, h = v
        if not (0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 and 0 < h <= 1):
            raise ValueError("box 是 [左, 上, 宽, 高]，取值 0～1")
        if x + w > 1.0001 or y + h > 1.0001:
            raise ValueError("box 超出了立绘范围")
        return v


RIG_PIVOTS = ("neck", "face", "elbow", "waist", "feet")


class _Rig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base: str = Field(min_length=1)
    iris: str = Field(min_length=1)
    weights: tuple[str, str, str]
    size: tuple[int, int]
    pivots: dict[str, tuple[float, float]]
    skirt: tuple[float, float]

    @field_validator("size")
    @classmethod
    def _size(cls, v: tuple[int, int]):
        if not all(16 <= c <= 8192 for c in v):
            raise ValueError("size 是 [宽, 高] 像素，16～8192")
        return v

    @field_validator("pivots")
    @classmethod
    def _pivots(cls, v: dict[str, tuple[float, float]]):
        missing = [k for k in RIG_PIVOTS if k not in v]
        unknown = sorted(set(v) - set(RIG_PIVOTS))
        if missing or unknown:
            raise ValueError(f"pivots 要有且只有 {list(RIG_PIVOTS)}")
        return v

    @field_validator("skirt")
    @classmethod
    def _skirt(cls, v: tuple[float, float]):
        if not v[0] < v[1]:
            raise ValueError("skirt 是 [上沿, 下沿]，上沿要小于下沿")
        return v


class _Variant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=12)
    image: str = Field(min_length=1)
    expressions: dict[str, _Expression] = Field(default_factory=dict)
    # 头部中心（0～1 比例）：摸头、眨眼摆动的支点；不写时用经验值
    head: tuple[float, float] | None = None
    # 底色是否需要去掉（纯色底的图默认去掉；已经是透明底时前端自动跳过）
    cutout: bool = True
    rig: _Rig | None = None

    @field_validator("expressions")
    @classmethod
    def _names(cls, v: dict[str, _Expression]):
        unknown = sorted(set(v) - set(EXPRESSIONS))
        if unknown:
            raise ValueError(f"不认识的表情 {unknown}，可用：{list(EXPRESSIONS)}")
        return v

    @field_validator("head")
    @classmethod
    def _head(cls, v: tuple[float, float] | None):
        if v is not None and not all(0 <= c <= 1 for c in v):
            raise ValueError("head 是 [横, 纵]，取值 0～1")
        return v


class _File(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int | None = None
    name: str = Field(min_length=1, max_length=20)
    persona: str | None = None
    variants: list[_Variant] = Field(min_length=1, max_length=MAX_VARIANTS)
    lines: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("lines")
    @classmethod
    def _lines(cls, v: dict[str, list[str]]):
        unknown = sorted(set(v) - set(LINE_KINDS))
        if unknown:
            raise ValueError(f"不认识的台词类别 {unknown}，可用：{list(LINE_KINDS)}")
        out = {}
        for kind, items in v.items():
            clean = [s.strip()[:MAX_LINE_CHARS] for s in items if isinstance(s, str) and s.strip()]
            out[kind] = clean[:MAX_LINES]
        return out


@dataclass(frozen=True)
class Variant:
    id: str
    label: str
    image: str  # 相对清单目录的 posix 路径
    expressions: dict[str, dict[str, Any]] = field(default_factory=dict)
    head: tuple[float, float] | None = None
    cutout: bool = True
    missing: tuple[str, ...] = ()  # 清单里写了但找不到文件的表情
    rig: dict[str, Any] | None = None  # 网格形变素材（文件都在时才有）


@dataclass(frozen=True)
class Standee:
    available: bool
    reason: str | None = None
    name: str = ""
    persona: str | None = None
    variants: tuple[Variant, ...] = ()
    lines: dict[str, list[str]] = field(default_factory=dict)
    root: Path | None = None


def _rel(root: Path, value: str) -> str | None:
    path = resolve_file(root, value)
    return path.relative_to(root.resolve()).as_posix() if path else None


def _rig(root: Path, rig: _Rig) -> dict[str, Any] | None:
    """rig 的图片都在才用；缺任何一张返回 None。"""
    files = [_rel(root, rig.base), _rel(root, rig.iris), *(_rel(root, w) for w in rig.weights)]
    if any(f is None for f in files):
        return None
    return {
        "base": files[0],
        "iris": files[1],
        "weights": files[2:],
        "size": list(rig.size),
        "pivots": {k: list(p) for k, p in rig.pivots.items()},
        "skirt": list(rig.skirt),
    }


def load_standee(manifest: Path | None) -> Standee:
    """读取立绘清单。任何问题都返回 available=False 与一句能看懂的原因。"""
    if manifest is None:
        return Standee(False, "没有设置立绘清单")
    try:
        if not manifest.is_file():
            return Standee(False, f"还没有立绘：把 {STANDEE_FILE} 放到 {manifest.parent.name}/ 里")
        if manifest.stat().st_size > MAX_BYTES:
            return Standee(False, f"{manifest.name} 太大")
        raw = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return Standee(False, f"{manifest.name} 无法读取：{type(exc).__name__}")
    try:
        data = _File.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        return Standee(False, f"{manifest.name} 第 {where} 项不对：{first['msg']}")
    root = manifest.parent
    variants = []
    for v in data.variants:
        image = _rel(root, v.image)
        if image is None:
            continue  # 这一套的图不在了：跳过这一套，别的照常
        exprs: dict[str, dict[str, Any]] = {}
        missing = []
        for name, e in v.expressions.items():
            rel = _rel(root, e.image)
            if rel is None:
                missing.append(name)
                continue
            exprs[name] = {"image": rel, "box": list(e.box) if e.box else None}
        rig = _rig(root, v.rig) if v.rig else None
        if v.rig and rig is None:
            missing.append("rig")
        variants.append(Variant(v.id, v.label, image, exprs, v.head, v.cutout, tuple(missing), rig))
    if not variants:
        return Standee(False, "清单里的立绘图片都找不到", name=data.name, root=root)
    if len({v.id for v in variants}) != len(variants):
        return Standee(False, f"{manifest.name} 里有重复的 id", name=data.name, root=root)
    return Standee(True, None, data.name, data.persona, tuple(variants), data.lines, root)


def standee_file(standee: Standee, rel: str) -> tuple[bytes, str] | None:
    """立绘目录内的图片（png / jpg / webp）；不在目录内或不存在时 None。"""
    found = resolve_file(standee.root, rel)
    if found is None:
        return None
    try:
        return found.read_bytes(), mime_of(found)
    except OSError:
        return None
