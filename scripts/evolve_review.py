"""革新 PR 的对抗式复审（可选）：把改动和项目规则交给一个外部模型挑错，输出缺陷清单。

给开发侧用（云端开 PR 之后跑），不是圆桌功能的一部分，也不读 .env：
key 只从环境变量 EVOLVE_LLM_API_KEY 读（放在云端环境里，专用 key、后台设月度上限）。
没有 key 或没有指定模型时**跳过并提示**，退出码 0，不影响其他步骤。

用法（在项目根目录）：
    python scripts/evolve_review.py --base origin/<开发分支> [-o 复审意见.md]

环境变量：
    EVOLVE_LLM_API_KEY    专用 key（必需，没有就跳过）
    EVOLVE_REVIEW_MODEL   该渠道上的模型 ID（必需，没有就跳过；不写进仓库）
    EVOLVE_LLM_CHANNEL    models.yaml 里的渠道名（可选；默认第一个 openai_compat 聚合渠道）
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import httpx

from roundtable.core.config import load_config
from roundtable.core.prompts import PromptLibrary
from roundtable.core.providers.base import Message
from roundtable.core.providers.errors import ProviderError
from roundtable.core.providers.openai_compat import OpenAICompatProvider
from roundtable.core.providers.secrets import Secret

ROOT = Path(__file__).resolve().parent.parent
KEY_ENV = "EVOLVE_LLM_API_KEY"
MODEL_ENV = "EVOLVE_REVIEW_MODEL"
CHANNEL_ENV = "EVOLVE_LLM_CHANNEL"
MAX_DIFF_CHARS = 120_000  # 约 4 万 token：单次复审约 $0.1～0.2（旗舰档价格）
MAX_RULES_CHARS = 12_000
MAX_TOKENS = 4000
WARN_USD = 0.50  # 单次超过就提醒（每月上限在 key 的后台设置）
TIMEOUT_S = 180


class Skip(Exception):
    """缺少可选条件：跳过复审（不是错误）。"""


def pick_channel(config, environ: Mapping[str, str]):
    name = environ.get(CHANNEL_ENV, "").strip()
    channels = config.models.channels
    if name:
        if name not in channels:
            raise Skip(f"{CHANNEL_ENV}={name!r} 不是 models.yaml 里的渠道")
        spec = channels[name]
    else:
        found = [
            (n, c)
            for n, c in channels.items()
            if c.adapter == "openai_compat" and c.kind == "aggregator"
        ]
        if not found:
            raise Skip("models.yaml 里没有 openai_compat 的聚合渠道")
        name, spec = found[0]
    if spec.adapter != "openai_compat":
        raise Skip(f"渠道 {name} 不是 openai_compat 接口，复审脚本只支持这一种")
    return name, spec


def clip(text: str, limit: int, what: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（{what}超过 {limit} 字，后面截掉了）"


def git_diff(base: str) -> str:
    out = subprocess.run(
        ["git", "diff", f"{base}...HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return out.stdout


async def review(
    diff: str,
    rules: str,
    *,
    environ: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, float | None]:
    """返回 (复审意见, 渠道报告的花费)。缺少 key / 模型时抛 Skip。"""
    raw_key = environ.get(KEY_ENV, "").strip()
    if not raw_key:
        raise Skip(f"没有 {KEY_ENV}")
    model = environ.get(MODEL_ENV, "").strip()
    if not model:
        raise Skip(f"没有 {MODEL_ENV}（复审用哪个模型）")
    if not diff.strip():
        raise Skip("没有改动")
    config = load_config()
    channel, spec = pick_channel(config, environ)
    version = config.roundtable.prompts["evolve_review"]
    rendered = PromptLibrary().render(
        "evolve_review",
        version,
        rules=clip(rules, MAX_RULES_CHARS, "项目规则"),
        diff=clip(diff, MAX_DIFF_CHARS, "改动"),
    )
    messages = [Message(m.role, m.content) for m in rendered.messages]
    provider = OpenAICompatProvider(channel, spec, Secret(raw_key), TIMEOUT_S, transport)
    try:
        result = await provider.complete(model, messages, {"max_tokens": MAX_TOKENS})
    finally:
        await provider.aclose()
    return result.text.strip(), result.reported_cost_usd


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="革新 PR 的对抗式复审（可选，没有 key 时跳过）")
    parser.add_argument("--base", required=True, help="对比的基准分支，如 origin/开发分支")
    parser.add_argument("-o", "--output", help="把复审意见写到这个文件")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        diff = git_diff(args.base)
        rules = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
        text, cost = asyncio.run(review(diff, rules, environ=env))
    except Skip as exc:
        print(f"跳过复审：{exc}。这一步是可选的，接 key 的方法见 docs/evolution/README.md。")
        return 0
    except subprocess.CalledProcessError:
        print(f"读不到改动：git diff {args.base}...HEAD 失败")
        return 2
    except ProviderError as exc:
        print(f"复审调用失败：{exc}")
        return 2
    cost_text = f"${cost:.4f}" if cost is not None else "渠道没有报告"
    print(f"复审花费：{cost_text}")
    if cost is not None and cost > WARN_USD:
        print(f"注意：单次复审超过 ${WARN_USD:.2f}，请检查改动是否过大")
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"已写到 {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
