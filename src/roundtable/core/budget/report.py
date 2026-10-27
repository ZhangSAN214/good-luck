"""余额与用量面板：把本机账本（calls 表）汇总成按天、按功能、按成员的用量，并把钱折算成 token。

纯计算，不调用模型、不联网。数据来自 Repository.usage_rows()；与预算守卫同一张表、同一个
UTC 时间窗，所以「本月已用」和顶栏、预算上限完全一致。

中立性：匿名开启且未揭晓的会话（hidden）只计入总数、按天和按功能，不按成员展示。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

# 功能分组（顺序即展示顺序）
FEATURES: dict[str, str] = {
    "work": "讨论作答",
    "office": "办公室",
    "chat": "聊天",
    "life": "成员生活",
    "evolution": "革新例会",
}
EVOLUTION_WORKFLOWS = frozenset({"evolution_scout"})
PER_MILLION = 1_000_000


@dataclass
class Totals:
    calls: int = 0
    failed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, row: Mapping[str, Any]) -> None:
        self.calls += int(row.get("calls", 0))
        self.failed += int(row.get("failed", 0))
        self.input_tokens += int(row.get("input_tokens", 0))
        self.output_tokens += int(row.get("output_tokens", 0))
        self.cached_tokens += int(row.get("cached_tokens", 0))
        self.reasoning_tokens += int(row.get("reasoning_tokens", 0))
        self.cost_usd += float(row.get("cost_usd", 0.0))

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["cost_usd"] = round(self.cost_usd, 6)
        return out


def feature_of(row: Mapping[str, Any], life_steps: Iterable[str]) -> str:
    """一行调用属于哪个功能：例会 / 找资料 → 革新例会；聊天会话里的生活步骤 → 成员生活。"""
    workflow = row.get("workflow") or ""
    if row.get("meeting") or workflow in EVOLUTION_WORKFLOWS:
        return "evolution"
    if workflow == "office":
        return "office"
    if workflow == "chat":
        return "life" if row.get("step") in set(life_steps) else "chat"
    return "work"


@dataclass
class UsageReport:
    totals: Totals = field(default_factory=Totals)
    by_feature: dict[str, Totals] = field(default_factory=dict)
    by_model: dict[str, Totals] = field(default_factory=dict)
    hidden: Totals = field(default_factory=Totals)  # 匿名未揭晓：不按成员拆
    by_channel: dict[str, Totals] = field(default_factory=dict)


def summarize(rows: Iterable[Mapping[str, Any]], life_steps: Iterable[str]) -> UsageReport:
    life = tuple(life_steps)
    report = UsageReport()
    features: dict[str, Totals] = defaultdict(Totals)
    models: dict[str, Totals] = defaultdict(Totals)
    channels: dict[str, Totals] = defaultdict(Totals)
    for row in rows:
        report.totals.add(row)
        features[feature_of(row, life)].add(row)
        if row.get("channel"):
            channels[row["channel"]].add(row)
        if row.get("hidden"):
            report.hidden.add(row)
        elif row.get("model_id"):
            models[row["model_id"]].add(row)
    report.by_feature = {k: features[k] for k in FEATURES if k in features}
    report.by_model = dict(sorted(models.items(), key=lambda kv: -kv[1].cost_usd))
    report.by_channel = dict(channels)
    return report


def daily(
    rows: Iterable[Mapping[str, Any]], end: date, days: int, life_steps: Iterable[str]
) -> list[dict[str, Any]]:
    """最近 days 天（含 end 当天，UTC 日期）每天的花费、token 与各功能花费；没调用的天为 0。"""
    life = tuple(life_steps)
    first = end - timedelta(days=days - 1)
    out = {
        (first + timedelta(days=i)).isoformat(): {"total": Totals(), "features": defaultdict(float)}
        for i in range(days)
    }
    for row in rows:
        slot = out.get(str(row.get("day")))
        if slot is None:
            continue
        slot["total"].add(row)
        slot["features"][feature_of(row, life)] += float(row.get("cost_usd", 0.0))
    return [
        {
            "day": day,
            **slot["total"].to_dict(),
            "features": {k: round(v, 6) for k, v in slot["features"].items()},
        }
        for day, slot in out.items()
    ]


def output_share(totals: Totals) -> float | None:
    """历史上输出 token 占全部 token 的比例（用于把钱折算成「混合」token）；没有记录为 None。"""
    both = totals.input_tokens + totals.output_tokens
    return totals.output_tokens / both if both else None


@dataclass(frozen=True)
class PriceRow:
    key: str  # 模型 id 或档位
    name: str
    tier: str | None
    input_price: float  # 美元 / 百万 token
    output_price: float


def token_equivalents(
    amount_usd: float, prices: Sequence[PriceRow], share: float | None
) -> list[dict[str, Any]]:
    """这笔钱按各自的价格大约能买多少 token：全用来读（输入）、全用来写（输出）、按历史比例混合。"""
    out = []
    for p in prices:
        blended = None
        if share is not None:
            per_m = (1 - share) * p.input_price + share * p.output_price
            blended = _tokens(amount_usd, per_m)
        out.append(
            {
                "key": p.key,
                "name": p.name,
                "tier": p.tier,
                "input_price": p.input_price,
                "output_price": p.output_price,
                "input_tokens": _tokens(amount_usd, p.input_price),
                "output_tokens": _tokens(amount_usd, p.output_price),
                "blended_tokens": blended,
            }
        )
    return out


def tier_averages(prices: Sequence[PriceRow], label: Callable[[str], str]) -> list[PriceRow]:
    """每个档位的平均价（简单平均，便宜的档在前）：给「节电档平均」「全力档平均」这类对照。"""
    groups: dict[str, list[PriceRow]] = defaultdict(list)
    for p in prices:
        if p.tier is not None:
            groups[p.tier].append(p)
    rows = [
        PriceRow(
            key=f"tier:{tier}",
            name=f"{label(tier)}平均",
            tier=tier,
            input_price=sum(p.input_price for p in rows) / len(rows),
            output_price=sum(p.output_price for p in rows) / len(rows),
        )
        for tier, rows in groups.items()
    ]
    return sorted(rows, key=lambda p: (p.output_price, p.input_price))  # 便宜的档在前


def _tokens(amount_usd: float, price_per_million: float) -> int | None:
    if price_per_million <= 0:
        return None  # 免费或没标价：说不清能用多少
    return int(max(0.0, amount_usd) / price_per_million * PER_MILLION)
