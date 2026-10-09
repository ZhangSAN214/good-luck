"""核对 config/models.yaml 中 OpenRouter 渠道的模型 ID 和价格（含图片、语音、转写、视频模型）。

用法（在项目根目录）：
    python scripts/check_models.py            # 读取 .env 中的 OPENROUTER_API_KEY（可选）
    python scripts/check_models.py --tolerance 0.05

按 token 计价的图像模型（配置了 image_tokens）的输出价格对照远端的 image_output。
发现模型不存在或价格偏差超出容差时，退出码为 1。本脚本只读，不修改任何文件。

媒体模型（seat: false）：先查 ID 是否存在（图片 / 语音走 /models 的 output_modalities 筛选，视频走
/videos/models）；按张 / 秒 / 分钟 / 字符计价的模型，远端价格字段因模型而异，脚本只把远端的
pricing 原样列出供人工核对（状态 manual，不影响退出码）。拿不到某个列表时标为 unverified。
"""

from __future__ import annotations

import argparse
import json
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
# 媒体模型不一定出现在默认的 /models 列表里：额外查询这些列表（失败只提示，不终止）
MEDIA_URLS = {
    "image": f"{MODELS_URL}?output_modalities=image",
    "audio": f"{MODELS_URL}?output_modalities=audio",
    "speech": f"{MODELS_URL}?output_modalities=speech",
    "video": "https://openrouter.ai/api/v1/videos/models",
}
PER_MILLION = 1_000_000


UNITS = {"image": "张", "second": "秒", "minute": "分钟", "char": "字符"}


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
        media_price = (m.media_price_for(route) if route else m.media_price) if not m.seat else None
        entries.append(
            {
                "id": m.id,
                "model": route.model if route else "-",
                "provider": CHANNEL if route else "other",
                "price": {"input": price.input, "output": price.output},
                "media_price": {"unit": media_price.unit, "usd": media_price.usd}
                if media_price
                else None,
                # 按 token 计价的图像模型：输出价格对应远端的 image_output
                "image_tokens": m.image_tokens,
            }
        )
    return entries


def fetch_remote(api_key: str | None, timeout: float = 30.0) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = httpx.get(MODELS_URL, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_media_remote(
    api_key: str | None, timeout: float = 30.0
) -> tuple[list[dict[str, Any]], list[str]]:
    """额外的媒体模型列表（合并）。返回 (模型, 没能获取的列表名)。"""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    found: list[dict[str, Any]] = []
    failed: list[str] = []
    for name, url in MEDIA_URLS.items():
        try:
            response = httpx.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            found += response.json().get("data", [])
        except (httpx.HTTPError, ValueError):
            failed.append(name)
    return found, failed


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
        if entry.get("media_price"):
            mp = entry["media_price"]
            raw = json.dumps(pricing or remote_model.get("pricing_skus"), ensure_ascii=False)
            findings.append(
                Finding(
                    model_id,
                    model,
                    "manual",
                    f"按{UNITS.get(mp['unit'], mp['unit'])}计价：配置 ${mp['usd']:g}；"
                    f"远端 pricing = {raw}",
                )
            )
            continue
        problems = []
        output_key = "image_output" if entry.get("image_tokens") else "completion"
        for side, remote_key in (("input", "prompt"), ("output", output_key)):
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

    api_key = os.environ.get("OPENROUTER_API_KEY") or None
    extra, failed = fetch_media_remote(api_key)
    merged = {"data": [*remote.get("data", []), *extra]}
    entries = load_local_models()
    findings = compare(entries, merged, args.tolerance)
    if failed:
        print(f"提示：没能获取这些媒体模型列表：{'、'.join(failed)}", file=sys.stderr)
        media_ids = {e["id"] for e in entries if e.get("media_price")}
        findings = [
            Finding(f.model_id, f.model, "unverified", "媒体列表获取失败，无法确认 ID 是否存在")
            if f.status == "missing" and f.model_id in media_ids
            else f
            for f in findings
        ]
    for f in findings:
        print(f"[{f.status:>14}] {f.model_id:<22} {f.model:<34} {f.detail}")
    return 1 if any(f.status in ("missing", "price_mismatch") for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
