"""下载工作台生成器的本地示例素材包（config/bench_samples.yaml）。

    python scripts/fetch_bench_samples.py           # 下载还没有的（只下清单里的，核对 sha256）
    python scripts/fetch_bench_samples.py --check   # 只看哪些已经有了
    python scripts/fetch_bench_samples.py --only portrait,coffee

存到 data/bench/_samples/（不进 git），连同一份 LICENSES.md（作者和许可证）。
你自己的图放进 data/bench/_samples/mine/，开工时也会带进成品（只给你自己用）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from roundtable.core.benchkit.samples import (  # noqa: E402
    SAMPLES_FILE,
    fetch_all,
    installed,
    load_catalog,
    pack_dir,
)
from roundtable.core.runtime import data_dir  # noqa: E402

MAX_BYTES = 40 * 1024 * 1024


async def _get(client: httpx.AsyncClient, url: str) -> bytes:
    r = await client.get(url, follow_redirects=True)
    r.raise_for_status()
    if len(r.content) > MAX_BYTES:
        raise ValueError("文件太大")
    return r.content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="下载工作台的本地示例素材包")
    parser.add_argument("--check", action="store_true", help="只检查，不下载")
    parser.add_argument("--only", default="", help="只下这几项（id，逗号分隔）")
    args = parser.parse_args(argv)
    catalog = load_catalog(ROOT / "config" / SAMPLES_FILE)
    root = data_dir() / "bench"
    if args.check:
        have = {s.id for s in installed(root, catalog)}
        for s in catalog.samples:
            print(("✓ " if s.id in have else "✗ ") + f"{s.id} {s.name}（{s.license}）")
        print(f"{len(have)}/{len(catalog.samples)} 项已在 {pack_dir(root)}")
        return 0 if len(have) == len(catalog.samples) else 1

    async def run() -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=120) as client:
            only = {x.strip() for x in args.only.split(",") if x.strip()} or None
            return await fetch_all(catalog, root, lambda u: _get(client, u), only=only)

    results = asyncio.run(run())
    mark = {"ok": "✓ 下载", "have": "✓ 已有", "bad": "✗ 校验", "error": "✗ 失败"}
    for r in results:
        print(f"{mark[r['state']]} {r['id']}：{r['detail']}")
    bad = [r for r in results if r["state"] in ("bad", "error")]
    print(f"完成：存在 {pack_dir(root)}" + (f"，{len(bad)} 项没下成" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
