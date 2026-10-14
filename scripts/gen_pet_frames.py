"""用图像模型（默认 Gemini 图像模型，走 OpenRouter）按立绘生成小人关键帧草稿。会产生费用。

用法（项目根目录，.env 里要有 OPENROUTER_API_KEY）：
    python scripts/gen_pet_frames.py --ref 立绘.png --member whale --action sign \\
        --frame "k0=下蹲蓄力，双手拿牌放膝盖上" --frame "k1=跳起，双臂高举过头" \\
        --out 草稿目录 --max-usd 0.5

每个 --frame 生成一张，文件名 <成员>_<动作>_k<序号>.png；已存在的不覆盖（跳过）。
累计费用达到 --max-usd 就停止。生成的只是草稿：之后要走 pose_process.py（色板吸附、二值化、对齐）
和 scripts/check_pet_art.py 才能进素材包。模型输出的像素风、色板、画布尺寸不保证一致，
所以先用 --frame 只出一张试效果。
"""

from __future__ import annotations

import argparse
import asyncio
import mimetypes
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = "openrouter"
FRAME_ARG = re.compile(r"^k(?P<n>\d+)=(?P<text>.+)$", re.S)

BASE_PROMPT = (
    "这是像素风 Q 版角色（参考图）。以参考图为唯一准绳：造型、服装、发型、配饰、"
    "像素颗粒、配色都不要改，"
    "背景完全透明。输出全身关键姿势，正方形画布，脚底在画布高度约 92%，角色水平居中。"
    "色板只用参考图里的颜色，不加平滑渐变，保持约 10 像素的块状颗粒，边缘清晰无半透明。"
    "只改姿势，不要画文字。本张姿势："
)


def parse_frames(items: list[str]) -> list[tuple[int, str]]:
    """把 'k0=描述' 解析成 (序号, 描述)；格式不对或序号重复时抛 ValueError。"""
    out: list[tuple[int, str]] = []
    seen: set[int] = set()
    for item in items:
        m = FRAME_ARG.match(item.strip())
        if not m:
            raise ValueError(f"--frame 格式应为 k<序号>=<描述>：{item!r}")
        n = int(m["n"])
        if n in seen:
            raise ValueError(f"序号 k{n} 重复")
        seen.add(n)
        out.append((n, m["text"].strip()))
    return sorted(out)


def build_prompt(description: str, extra: str = "") -> str:
    return BASE_PROMPT + description + (f"。{extra}" if extra else "")


def target_path(out: Path, member: str, action: str, n: int, mime: str) -> Path:
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(mime, "png")
    return out / f"{member}_{action}_k{n}.{ext}"


async def generate(args: argparse.Namespace, provider, model, route, refs) -> int:
    frames = parse_frames(args.frame)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    done = 0
    for n, desc in frames:
        existing = list(out.glob(f"{args.member}_{args.action}_k{n}.*"))
        if existing:
            print(f"跳过 k{n}：{existing[0].name} 已存在")
            continue
        if spent >= args.max_usd:
            print(f"已花费 {spent:.3f} 美元，达到上限 {args.max_usd}，停止")
            break
        prompt = build_prompt(desc, args.extra)
        api = model.image_api_for(route)
        result = await provider.generate_image(
            route.model, prompt, model.params_for(route), images=refs, api=api
        )
        image = result.images[0]
        path = target_path(out, args.member, args.action, n, image.mime)
        path.write_bytes(image.data)
        spent += float(result.cost_usd or 0)
        done += 1
        print(f"k{n} -> {path.name}（费用 {result.cost_usd}）")
    print(f"完成 {done} 张，累计 {spent:.3f} 美元")
    return 0


async def run(args: argparse.Namespace) -> int:
    from dotenv import load_dotenv

    from roundtable.core.config import load_config
    from roundtable.core.providers import Media, ProviderError, Secret
    from roundtable.core.providers.openai_compat import OpenAICompatProvider

    try:
        parse_frames(args.frame)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    load_dotenv(ROOT / ".env")
    config = load_config(ROOT / "config")
    spec = config.models.channels[CHANNEL]
    key = os.environ.get(spec.key_env or "")
    if not key:
        print(f"缺少 {spec.key_env}（写在 .env 里）", file=sys.stderr)
        return 1
    model = config.models.get(args.model)
    route = next((r for r in model.routes if r.channel == CHANNEL), None)
    if route is None:
        print(f"{args.model} 没有 {CHANNEL} 路由", file=sys.stderr)
        return 1
    ref = Path(args.ref)
    mime = mimetypes.guess_type(ref.name)[0] or "image/png"
    refs = [Media("image", mime, ref.read_bytes(), ref.name)]
    provider = OpenAICompatProvider(CHANNEL, spec, Secret(key), timeout_s=180)
    try:
        return await generate(args, provider, model, route, refs)
    except ProviderError as exc:
        print(f"失败：{exc.kind.value} {exc}", file=sys.stderr)
        return 1
    finally:
        await provider.aclose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ref", required=True, help="立绘文件")
    ap.add_argument("--member", required=True, help="文件名前缀，如 whale")
    ap.add_argument("--action", required=True, help="动作名，如 sign / idle")
    ap.add_argument("--frame", action="append", required=True, help="k<序号>=<姿势描述>，可多次")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--model", default="gemini-3.1-flash-image", help="models.yaml 中的图像模型 id")
    ap.add_argument("--extra", default="", help="附加到每张提示词末尾的话")
    ap.add_argument("--max-usd", type=float, default=0.5, help="累计费用上限（美元）")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
