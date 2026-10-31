"""工作台生成器的本地示例素材包（config/bench_samples.yaml，2026-10-31 工作台大更新）。

修图台那次示例照片全从网上取，成品里所有照片都「打不开」，10 条验收有 8 条没法验。现在：

- 素材清单写在配置里，每项钉死 sha256、写明许可证（只收 CC0 / 公有领域 / 注明作者的 CC-BY）。
- 下载由 ``scripts/fetch_bench_samples.py`` 在电脑上做：只下载清单里的、对不上 sha256 的丢掉；
  存到 ``data/bench/_samples/``（不进仓库），同时写一份 ``LICENSES.md`` 注明作者。
- 开工时把本机已有的素材复制进成品的 ``assets/samples/``，清单交给写窗口的助手：要示例图、
  3D 模型时用这些本地文件，在线素材只作补充。你自己放进 ``_samples/mine/`` 的图也会带上。
- 只读文件、只做 GET；网络请求走注入的函数（测试换成假的，不联网）。
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from roundtable.core.config.loader import (
    DEFAULT_CONFIG_DIR,
    ConfigError,
    _format_errors,
    _read_yaml,
)

SAMPLES_FILE = "bench_samples.yaml"
PACK = "_samples"  # data/bench/_samples/
MINE = "mine"  # 你自己放的图：data/bench/_samples/mine/
TARGET = "assets/samples"  # 成品里的位置
MINE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".glb", ".gltf", ".mp4", ".webm"}
MAX_MINE = 24
Kind = Literal["photo", "model", "texture", "video", "audio", "icon", "font"]
KIND_LABELS = {
    "photo": "照片",
    "model": "3D 模型",
    "texture": "贴图",
    "video": "视频",
    "audio": "声音",
    "icon": "图标",
    "font": "字体",
}


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,40}$")
    name: str = Field(min_length=1, max_length=60)
    kind: Kind
    file: str
    url: str = Field(pattern=r"^https://")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    license: str = Field(min_length=1)
    author: str = Field(min_length=1)
    attribution: str = ""
    tags: list[str] = []

    @field_validator("file")
    @classmethod
    def _relative(cls, v: str) -> str:
        p = PurePosixPath(v)
        if p.is_absolute() or ".." in p.parts or not p.suffix:
            raise ValueError("要写成 photos/xxx.png 这样的相对路径")
        return str(p)


class SampleCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    samples: list[Sample] = []

    @field_validator("samples")
    @classmethod
    def _unique(cls, v: list[Sample]) -> list[Sample]:
        for key in ("id", "file"):
            seen: set[str] = set()
            for s in v:
                val = getattr(s, key)
                if val in seen:
                    raise ValueError(f"{key} 重复：{val}")
                seen.add(val)
        return v


def load_catalog(path: Path | str = DEFAULT_CONFIG_DIR / SAMPLES_FILE) -> SampleCatalog:
    """读素材清单；文件不在就是空清单。多余字段、路径不对都报错（带文件名和字段路径）。"""
    path = Path(path)
    if not path.is_file():
        return SampleCatalog()
    raw = _read_yaml(path) or {}
    try:
        return SampleCatalog.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(_format_errors(path.name, exc)) from None


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pack_dir(root: Path) -> Path:
    return root / PACK


def installed(root: Path, catalog: SampleCatalog) -> list[Sample]:
    """本机已经下载好的素材（文件在就算；下载时已核对过 sha256）。"""
    base = pack_dir(root)
    return [s for s in catalog.samples if (base / s.file).is_file()]


def mine(root: Path) -> list[Path]:
    """你自己放进 _samples/mine/ 的文件（按文件名排，最多 MAX_MINE 个）。"""
    d = pack_dir(root) / MINE
    if not d.is_dir():
        return []
    files = [p for p in sorted(d.iterdir()) if p.is_file() and p.suffix.lower() in MINE_EXT]
    return files[:MAX_MINE]


def licenses_markdown(items: list[Sample]) -> str:
    lines = ["# 示例素材的来源和许可证", "", "这些文件只用作工作台里的示例。", ""]
    for s in items:
        credit = s.attribution or f"作者：{s.author}"
        lines.append(f"- `{s.file}` {s.name}：{s.license}。{credit}。来源：{s.url}")
    return "\n".join(lines) + "\n"


Fetch = Callable[[str], Awaitable[bytes]]


async def fetch_all(
    catalog: SampleCatalog, root: Path, get: Fetch, *, only: set[str] | None = None
) -> list[dict[str, str]]:
    """下载还没有的素材：sha256 对上才存；返回每项的结果（ok / have / bad / error）。"""
    base = pack_dir(root)
    out: list[dict[str, str]] = []
    for s in catalog.samples:
        if only and s.id not in only:
            continue
        dest = base / s.file
        if dest.is_file() and sha256_of(dest.read_bytes()) == s.sha256:
            out.append({"id": s.id, "state": "have", "detail": "已经有了"})
            continue
        try:
            data = await get(s.url)
        except Exception as exc:  # noqa: BLE001 - 下载失败只记下来，接着下别的
            out.append({"id": s.id, "state": "error", "detail": str(exc)[:160]})
            continue
        if sha256_of(data) != s.sha256:
            out.append({"id": s.id, "state": "bad", "detail": "sha256 对不上，没存"})
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        out.append({"id": s.id, "state": "ok", "detail": f"{len(data) // 1024} KB"})
    have = installed(root, catalog)
    if have:
        base.mkdir(parents=True, exist_ok=True)
        (base / "LICENSES.md").write_text(licenses_markdown(have), encoding="utf-8")
    return out


def install(root: Path, folder: Path, catalog: SampleCatalog) -> list[dict[str, str]]:
    """把本机已有的素材复制进成品的 assets/samples/，返回给助手看的清单（成品里的相对路径）。"""
    items: list[dict[str, str]] = []
    have = installed(root, catalog)
    target = folder / TARGET
    for s in have:
        dest = target / s.file
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pack_dir(root) / s.file, dest)
        items.append(
            {
                "file": f"{TARGET}/{s.file}",
                "name": s.name,
                "kind": s.kind,
                "license": s.license,
                "credit": s.attribution or s.author,
                "tags": "、".join(s.tags),
            }
        )
    for p in mine(root):
        dest = target / MINE / p.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
        kind = (
            "model"
            if p.suffix.lower() in {".glb", ".gltf"}
            else ("video" if p.suffix.lower() in {".mp4", ".webm"} else "photo")
        )
        items.append(
            {
                "file": f"{TARGET}/{MINE}/{p.name}",
                "name": p.stem,
                "kind": kind,
                "license": "你自己的",
                "credit": "你",
                "tags": "你放的",
            }
        )
    if have:
        target.mkdir(parents=True, exist_ok=True)
        (target / "LICENSES.md").write_text(licenses_markdown(have), encoding="utf-8")
    return items


def samples_text(items: list[dict[str, str]]) -> str:
    """写窗口、返工时给助手看的一段：有哪些本地素材、在哪、能拿来干什么。"""
    if not items:
        return (
            "（本机还没有示例素材包。需要示例照片或模型时，先用用户拖进来的素材；"
            "都没有就用空状态加一个「导入」按钮，不要在线取图，也不要自己画。）"
        )
    lines = []
    for x in items:
        kind = KIND_LABELS.get(x["kind"], x["kind"])
        lines.append(f"- {x['file']}：{kind} · {x['name']}（{x['tags']}；{x['license']}）")
    return "\n".join(lines)
