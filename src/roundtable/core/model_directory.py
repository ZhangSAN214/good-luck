"""模型图鉴：聚合渠道上的全部模型，按输出类型分类、按能力打标签，并标出哪些已在圆桌里。

- 分类只看远端列表的输出 / 输入类型、支持的参数和价格字段，不针对任何具体模型或厂商写分支。
- 只用于浏览：图鉴里的模型不会上桌，也不会写进 models.yaml。界面可以生成一段「默认停用」的
  配置片段供用户自己复制；改上桌名单和价格都由用户决定。
- 远端的英文简介原样截断展示，不调用模型翻译（不花钱）；中文说明在分类和标签上。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .catalog import PER_MILLION, Fetch, _per_million, _suggest_tier, _tier_bands, http_fetch
from .config import AppConfig

STALE_AFTER_S = 24 * 3600
DESCRIPTION_MAX = 240
LONG_CONTEXT = 200_000
HUGE_CONTEXT = 1_000_000

# 分类（顺序即界面顺序）：键 → (名称, 一句话说明)
CATEGORIES: dict[str, tuple[str, str]] = {
    "text": ("文字对话", "读文字（部分能看图、听音频）、写文字，圆桌的上桌成员都在这一类。"),
    "image": ("绘图", "按文字或参考图生成、修改图片。"),
    "video": ("视频生成", "按文字或图片生成短视频，部分自带配音和音效；通常按秒计价。"),
    "speech": ("语音合成", "把文字读出来（配音、朗读），可选音色；通常按字符计价。"),
    "voice_chat": ("语音对话", "既能输出文字也能直接输出语音的对话模型。"),
    "audio": ("音乐与音频", "生成音乐、配乐或其他音频（不是朗读）。"),
    "transcription": ("语音转文字", "把音频转写成文字。"),
    "embeddings": ("向量", "把文字变成向量，用于检索、相似度、记忆召回；不会写文字。"),
    "rerank": ("重排序", "给搜索结果按相关度重新排序。"),
    "other": ("其他", "审核、路由判断等不属于以上类别的模型。"),
}

# 标签：键 → (名称, 说明)
TAGS: dict[str, tuple[str, str]] = {
    "free": ("免费", "当前不收费（通常限流，可能随时下架）。"),
    "vision": ("看图", "输入可以带图片。"),
    "audio_in": ("听音频", "输入可以带音频。"),
    "video_in": ("看视频", "输入可以带视频。"),
    "file_in": ("读文件", "输入可以直接带 PDF 等文件。"),
    "tools": ("工具调用", "支持函数 / 工具调用，可以用搜索、代码等工具。"),
    "reasoning": ("深度思考", "支持思考 / 推理模式。"),
    "structured": ("结构化输出", "可以按指定 JSON 格式输出。"),
    "long_context": ("长上下文", f"上下文 ≥ {LONG_CONTEXT // 1000}K token。"),
    "huge_context": ("超长上下文", f"上下文 ≥ {HUGE_CONTEXT // 1_000_000}M token。"),
    "variant": ("变体", "同一模型的特殊版本（免费限流版、加速版等，id 带冒号后缀）。"),
}

# 文字模型的价格段（按输出价格，美元 / 百万 token）：上限（不含）→ 名称
PRICE_BANDS: list[tuple[float, str, str]] = [
    (1.0, "cheap", "便宜（< $1）"),
    (5.0, "mid", "中等（$1～5）"),
    (20.0, "pricey", "较贵（$5～20）"),
    (float("inf"), "premium", "旗舰价（≥ $20）"),
]

# 远端价格字段 → (名称, 单位)。prompt / completion 单独换算成每百万 token
PRICE_FIELDS: dict[str, tuple[str, str]] = {
    "request": ("每次请求", "次"),
    "image": ("每张输入图", "张"),
    "image_output": ("每张输出图", "张"),
    "image_token": ("图像 token", "token"),
    "audio": ("音频输入", "token"),
    "audio_output": ("音频输出", "token"),
    "web_search": ("每次联网搜索", "次"),
    "internal_reasoning": ("思考 token", "token"),
}


@dataclass
class DirectoryEntry:
    route: str
    name: str
    author: str  # 路由 id 的前缀（远端数据，不写死）
    created: int
    category: str
    tags: list[str]
    input_modalities: list[str]
    output_modalities: list[str]
    context_length: int | None
    price: tuple[float | None, float | None]  # 每百万 token（输入, 输出）
    # PRICE_FIELDS 中非零的项（美元 / 单位；token 类换算成每百万）
    extra_prices: dict[str, float] = field(default_factory=dict)
    price_band: str | None = None  # 只对文字模型
    suggested_tier: str | None = None  # 按圆桌各档位的价格区间建议（只对文字模型）
    description: str = ""
    in_roundtable: dict[str, Any] | None = None  # {model_id, seat, enabled, tier}


@dataclass
class Directory:
    fetched_at: float
    source: str
    entries: list[DirectoryEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        tag_counts: dict[str, int] = {}
        for e in self.entries:
            counts[e.category] = counts.get(e.category, 0) + 1
            for t in e.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1
        return {
            "fetched_at": self.fetched_at,
            "source": self.source,
            "total": len(self.entries),
            "categories": [
                {"key": k, "label": v[0], "about": v[1], "count": counts.get(k, 0)}
                for k, v in CATEGORIES.items()
            ],
            "tags": [
                {"key": k, "label": v[0], "about": v[1], "count": tag_counts.get(k, 0)}
                for k, v in TAGS.items()
            ],
            "price_bands": [{"key": k, "label": label} for _, k, label in PRICE_BANDS],
            "price_fields": {k: {"label": v[0], "unit": v[1]} for k, v in PRICE_FIELDS.items()},
            "entries": [asdict(e) for e in self.entries],
        }


def _list(value: Any) -> list[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def categorize(remote: dict[str, Any]) -> str:
    """按输出类型分类。"""
    arch = remote.get("architecture") or {}
    out = set(_list(arch.get("output_modalities"))) or {"text"}
    if "video" in out:
        return "video"
    if "image" in out:
        return "image"
    if "embeddings" in out:
        return "embeddings"
    if "rerank" in out:
        return "rerank"
    if "transcription" in out:
        return "transcription"
    if out & {"speech", "audio"}:
        if "text" in out:
            return "voice_chat"
        if "speech" in out or remote.get("supported_voices"):
            return "speech"
        return "audio"
    if "text" in out:
        return "text"
    return "other"


def tags_of(remote: dict[str, Any]) -> list[str]:
    arch = remote.get("architecture") or {}
    inputs = set(_list(arch.get("input_modalities")))
    params = set(_list(remote.get("supported_parameters")))
    pricing = remote.get("pricing") or {}
    rid = str(remote.get("id") or "")
    found: list[str] = []
    paid = [_number(v) for k, v in pricing.items() if k != "discount"]
    if rid.endswith(":free") or (paid and all(p == 0 for p in paid if p is not None)):
        found.append("free")
    if "image" in inputs:
        found.append("vision")
    if "audio" in inputs:
        found.append("audio_in")
    if "video" in inputs:
        found.append("video_in")
    if "file" in inputs:
        found.append("file_in")
    if "tools" in params:
        found.append("tools")
    if params & {"reasoning", "include_reasoning"} or remote.get("reasoning"):
        found.append("reasoning")
    if params & {"response_format", "structured_outputs"}:
        found.append("structured")
    ctx = remote.get("context_length")
    if isinstance(ctx, int) and ctx >= LONG_CONTEXT:
        found.append("long_context")
    if isinstance(ctx, int) and ctx >= HUGE_CONTEXT:
        found.append("huge_context")
    if ":" in rid:
        found.append("variant")
    return found


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _price_band(output: float | None) -> str | None:
    if output is None:
        return None
    for limit, key, _ in PRICE_BANDS:
        if output < limit:
            return key
    return None


def _description(text: Any) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= DESCRIPTION_MAX else s[: DESCRIPTION_MAX - 1].rstrip() + "…"


def build_directory(
    config: AppConfig, remote: dict[str, Any], *, source: str, now: float | None = None
) -> Directory:
    """把远端模型列表整理成图鉴。纯函数，便于测试。"""
    directory = Directory(fetched_at=time.time() if now is None else now, source=source)
    configured: dict[str, dict[str, Any]] = {}
    for m in config.models.models:
        for r in m.routes:
            if r.channel == source:
                configured[r.model] = {
                    "model_id": m.id,
                    "seat": m.seat,
                    "enabled": m.enabled,
                    "tier": m.tier,
                }
    bands = _tier_bands(config, source)
    seen: set[str] = set()
    for r in remote.get("data", []):
        rid = r.get("id")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        pricing = r.get("pricing") or {}
        price = (_per_million(pricing.get("prompt")), _per_million(pricing.get("completion")))
        extra = {}
        for key in PRICE_FIELDS:
            v = _number(pricing.get(key))
            if v:
                extra[key] = v * PER_MILLION if PRICE_FIELDS[key][1] == "token" else v
        category = categorize(r)
        arch = r.get("architecture") or {}
        is_text = category == "text"
        directory.entries.append(
            DirectoryEntry(
                route=rid,
                name=str(r.get("name") or rid),
                author=rid.split("/", 1)[0] if "/" in rid else "",
                created=int(r.get("created") or 0),
                category=category,
                tags=tags_of(r),
                input_modalities=_list(arch.get("input_modalities")),
                output_modalities=_list(arch.get("output_modalities")) or ["text"],
                context_length=r.get("context_length")
                if isinstance(r.get("context_length"), int)
                else None,
                price=price,
                extra_prices=extra,
                price_band=_price_band(price[1]) if is_text else None,
                suggested_tier=_suggest_tier(price[1], bands)
                if is_text and price[1] is not None and price[1] > 0
                else None,
                description=_description(r.get("description")),
                in_roundtable=configured.get(rid),
            )
        )
    order = {k: i for i, k in enumerate(CATEGORIES)}
    directory.entries.sort(key=lambda e: (order.get(e.category, 99), -e.created, e.route))
    return directory


class ModelDirectory:
    """缓存最近一次的图鉴（JSON 文件，放在数据目录，不进仓库）。"""

    def __init__(
        self,
        config: AppConfig,
        path: Path | None,
        fetch: Fetch | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.path = path
        self.fetch = fetch or http_fetch
        self.clock = clock
        self._memory: dict[str, Any] | None = None

    def _source(self) -> tuple[str, str] | None:
        """第一个聚合渠道的 (名称, 全部模型的列表地址)。"""
        for name, ch in self.config.models.channels.items():
            if ch.kind == "aggregator":
                return name, ch.base_url.rstrip("/") + "/models?output_modalities=all"
        return None

    def cached(self) -> dict[str, Any] | None:
        if self._memory is None and self.path and self.path.exists():
            try:
                self._memory = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._memory = None
        return self._memory

    def view(self) -> dict[str, Any]:
        data = self.cached()
        available = self._source() is not None
        if data is None:
            return {"fetched_at": None, "stale": True, "available": available}
        age = self.clock() - float(data.get("fetched_at") or 0)
        return {**data, "stale": age > STALE_AFTER_S, "available": available}

    async def refresh(self) -> dict[str, Any]:
        found = self._source()
        if found is None:
            raise LookupError("没有配置聚合渠道，无法查询模型列表")
        name, url = found
        remote = await self.fetch(url)
        data = build_directory(self.config, remote, source=name, now=self.clock()).to_dict()
        self._memory = data
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return self.view()
