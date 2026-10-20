"""模型人才市场：从聚合渠道的公开模型列表发现更新、更合适的模型，只提示，不改配置。

- 价格变动：已配置模型在聚合渠道上的实际价格与 models.yaml 不符。
- 下架：已配置的路由在聚合渠道上找不到。
- 新人：与已配置模型同一系列（路由 id 的前缀，如 ``系列/模型``）、
  发布时间比该系列所有已配置模型都新、能输出文字的模型；按输出价格对照各档位已配置模型的价格区间，建议它进哪一档、可替换谁。

所有判断只看配置里的路由 id、档位和价格，不针对任何具体模型或厂商写分支。
结果只用于展示：改价格、换人上桌都要用户自己改 models.yaml（或明确确认后再改）。
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import AppConfig

PER_MILLION = 1_000_000
STALE_AFTER_S = 24 * 3600  # 超过一天的结果视为过期，界面会自动刷新
PRICE_TOLERANCE = 0.02
BAND_SLACK = 1.5  # 档位价格区间上下各放宽的倍数
MAX_PER_FAMILY = 3

Fetch = Callable[[str], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class PriceChange:
    model_id: str
    route: str
    configured: tuple[float, float]
    actual: tuple[float, float]


@dataclass(frozen=True)
class Candidate:
    route: str
    name: str
    family: str
    created: int
    price: tuple[float, float]
    context_length: int | None
    vision: bool
    tier: str | None  # 建议的档位；价格不在任何档位区间内时为 None
    replaces: list[str] = field(default_factory=list)  # 同系列、同档位的已配置模型


@dataclass
class CatalogReport:
    fetched_at: float
    source: str
    price_changes: list[PriceChange] = field(default_factory=list)
    missing: list[dict[str, str]] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregator_channel(config: AppConfig) -> tuple[str, str] | None:
    """第一个聚合渠道的 (名称, 模型列表地址)；没有聚合渠道时为 None。"""
    for name, ch in config.models.channels.items():
        if ch.kind == "aggregator":
            return name, ch.base_url.rstrip("/") + "/models"
    return None


def _per_million(value: Any) -> float | None:
    try:
        return round(float(value) * PER_MILLION, 6)
    except (TypeError, ValueError):
        return None


def _family(route_model: str) -> str | None:
    return route_model.split("/", 1)[0] if "/" in route_model else None


def _outputs_text(remote: dict[str, Any]) -> bool:
    out = (remote.get("architecture") or {}).get("output_modalities")
    return out is None or "text" in out


def _tier_bands(config: AppConfig, channel: str) -> dict[str, tuple[float, float]]:
    """各档位已配置上桌模型的输出价格区间（上下各放宽 BAND_SLACK 倍）。"""
    prices: dict[str, list[float]] = {}
    for m in config.models.models:
        if not m.seat or not m.enabled or not m.tier:
            continue
        route = next((r for r in m.routes if r.channel == channel), None)
        price = m.price_for(route) if route else m.price
        prices.setdefault(m.tier, []).append(price.output)
    return {t: (min(p) / BAND_SLACK, max(p) * BAND_SLACK) for t, p in prices.items() if p}


def _suggest_tier(output: float, bands: dict[str, tuple[float, float]]) -> str | None:
    inside = [t for t, (lo, hi) in bands.items() if lo <= output <= hi]
    if not inside:
        return None

    # 落在多个区间时取对数距离最近的区间中点
    def distance(t: str) -> float:
        lo, hi = bands[t]
        mid = math.sqrt(max(lo, 1e-9) * max(hi, 1e-9))
        return abs(math.log(max(output, 1e-9) / mid))

    return min(sorted(inside), key=distance)


def analyze(
    config: AppConfig, remote: dict[str, Any], *, source: str, now: float | None = None
) -> CatalogReport:
    """对照配置与远端列表，得出价格变动、下架与新人候选。纯函数，便于测试。"""
    report = CatalogReport(fetched_at=time.time() if now is None else now, source=source)
    remote_by_id = {m.get("id"): m for m in remote.get("data", []) if m.get("id")}
    known = {r.model for m in config.models.models for r in m.routes if r.channel == source}

    newest: dict[str, int] = {}  # 系列 → 已配置模型中最新的发布时间
    by_family: dict[str, list[Any]] = {}  # 系列 → 已配置的上桌模型
    for m in config.models.models:
        route = next((r for r in m.routes if r.channel == source), None)
        if route is None:
            continue
        found = remote_by_id.get(route.model)
        if found is None:
            report.missing.append({"model_id": m.id, "route": route.model})
            continue
        if not m.seat:
            continue  # 媒体模型计价方式各异，价格由 scripts/check_models.py 人工核对
        family = _family(route.model)
        if family:
            newest[family] = max(newest.get(family, 0), int(found.get("created") or 0))
            if m.enabled:
                by_family.setdefault(family, []).append(m)
        pricing = found.get("pricing") or {}
        actual = (_per_million(pricing.get("prompt")), _per_million(pricing.get("completion")))
        price = m.price_for(route)
        configured = (price.input, price.output)
        if None in actual:
            continue
        pairs = zip(actual, configured, strict=True)
        if any(abs(a - c) > PRICE_TOLERANCE * max(a, 1e-9) for a, c in pairs):
            report.price_changes.append(PriceChange(m.id, route.model, configured, actual))  # type: ignore[arg-type]

    bands = _tier_bands(config, source)
    for family, baseline in newest.items():
        fresh = []
        for rid, r in remote_by_id.items():
            if rid in known or ":" in rid or _family(rid) != family:
                continue  # ":" 是同一模型的变体（免费限流版等）
            if int(r.get("created") or 0) <= baseline or not _outputs_text(r):
                continue
            pricing = r.get("pricing") or {}
            price = (_per_million(pricing.get("prompt")), _per_million(pricing.get("completion")))
            if None in price:
                continue
            tier = _suggest_tier(price[1], bands)  # type: ignore[arg-type]
            replaces = sorted(m.id for m in by_family.get(family, []) if m.tier == tier)
            modalities = (r.get("architecture") or {}).get("input_modalities") or []
            fresh.append(
                Candidate(
                    route=rid,
                    name=str(r.get("name") or rid),
                    family=family,
                    created=int(r.get("created") or 0),
                    price=price,  # type: ignore[arg-type]
                    context_length=r.get("context_length"),
                    vision="image" in modalities,
                    tier=tier,
                    replaces=replaces,
                )
            )
        fresh.sort(key=lambda c: (-c.created, c.route))
        report.candidates += fresh[:MAX_PER_FAMILY]
    report.candidates.sort(key=lambda c: (-c.created, c.route))
    report.price_changes.sort(key=lambda p: p.model_id)
    return report


async def http_fetch(url: str, timeout: float = 30.0) -> dict[str, Any]:
    """公开的模型列表不需要 key。"""
    import httpx

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


class ModelCatalog:
    """缓存最近一次的人才市场结果（JSON 文件，放在数据目录，不进仓库）。"""

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

    def cached(self) -> dict[str, Any] | None:
        if self._memory is None and self.path and self.path.exists():
            try:
                self._memory = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._memory = None
        return self._memory

    def view(self) -> dict[str, Any]:
        data = self.cached()
        source = aggregator_channel(self.config)
        if data is None:
            return {"fetched_at": None, "stale": True, "available": source is not None}
        age = self.clock() - float(data.get("fetched_at") or 0)
        return {**data, "stale": age > STALE_AFTER_S, "available": source is not None}

    async def refresh(self) -> dict[str, Any]:
        found = aggregator_channel(self.config)
        if found is None:
            raise LookupError("没有配置聚合渠道，无法查询模型列表")
        name, url = found
        remote = await self.fetch(url)
        data = analyze(self.config, remote, source=name, now=self.clock()).to_dict()
        self._memory = data
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.view()
