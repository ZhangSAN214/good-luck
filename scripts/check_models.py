"""核对 config/models.yaml 中 OpenRouter 渠道的模型 ID 和价格。

用法（在项目根目录）：
    python scripts/check_models.py            # 读取 .env 中的 OPENROUTER_API_KEY（可选）
    python scripts/check_models.py --tolerance 0.05

发现模型不存在或价格偏差超出容差时，退出码为 1。本脚本只读，不修改任何文件。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from roundtable.core.config import load_config

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = "openrouter"
MODELS_URL = "https://openrouter.ai/api/v1/models"
PER_MILLION = 1_000_000


@dataclass(frozen=True)
class Finding:
    model_id: str
    model: str
    status: str  # ok | missing | price_mismatch | skipped
    detail: str = ""


def load_local_models(config_dir: Path | None = None) -> list[dict[str, Any]]:
    """每个模型取 OpenRouter 渠道的路由；没有该渠道的模型标为 skipped。"""
    cfg = load_config(config_dir) if config_dir else load_config()
    entries = []
    for m in cfg.models.models:
        route = next((r for r in m.routes if r.channel == CHANNEL), None)
        price = m.price_for(route) if route else m.price
        entries.append(
            {
                "id": m.id,
                "model": route.model if route else "-",
                "provider": CHANNEL if route else "other",
                "price": {"input": price.input, "output": price.output},
            }
        )
    return entries


def fetch_remote(api_key: str | None, timeout: float = 30.0) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = httpx.get(MODELS_URL, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def _per_million(value: Any) -> float | None:
    try:
        return float(value) * PER_MILLION
    except (TypeError, ValueError):
        return None


def compare(
    local: list[dict[str, Any]], remote: dict[str, Any], tolerance: float = 0.01
) -> list[Finding]:
    """逐个比对本地模型。tolerance 为价格相对偏差上限。"""
    remote_by_id = {m.get("id"): m for m in remote.get("data", [])}
    findings: list[Finding] = []
    for entry in local:
        model_id, model = entry.get("id", "?"), entry.get("model", "?")
        if entry.get("provider") != "openrouter":
            findings.append(Finding(model_id, model, "skipped", "未配置 openrouter 渠道"))
            continue
        remote_model = remote_by_id.get(model)
        if remote_model is None:
            findings.append(Finding(model_id, model, "missing", "OpenRouter 上找不到该 ID"))
            continue
        pricing = remote_model.get("pricing", {})
        price = entry.get("price", {})
        problems = []
        for side, remote_key in (("input", "prompt"), ("output", "completion")):
            actual = _per_million(pricing.get(remote_key))
            expected = price.get(side)
            if actual is None or expected is None:
                problems.append(f"{side} 价格缺失")
            elif abs(actual - expected) > tolerance * max(actual, 1e-9):
                problems.append(f"{side}: 配置 {expected:g}，实际 {actual:g}")
        if problems:
            findings.append(Finding(model_id, model, "price_mismatch", "；".join(problems)))
        else:
            findings.append(Finding(model_id, model, "ok"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tolerance", type=float, default=0.01, help="价格相对偏差容差")
    args = parser.parse_args(argv)

    load_dotenv(ROOT / ".env")
    try:
        remote = fetch_remote(os.environ.get("OPENROUTER_API_KEY") or None)
    except httpx.HTTPError as exc:
        # 只输出异常类型，避免把请求头等信息打印出来
        print(f"获取 OpenRouter 模型列表失败：{type(exc).__name__}", file=sys.stderr)
        return 2

    findings = compare(load_local_models(), remote, args.tolerance)
    for f in findings:
        print(f"[{f.status:>14}] {f.model_id:<22} {f.model:<34} {f.detail}")
    return 1 if any(f.status in ("missing", "price_mismatch") for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
