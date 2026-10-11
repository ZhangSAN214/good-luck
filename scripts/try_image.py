"""用真实的 OpenRouter 调一次画图模型，核对接口格式（会产生少量费用）。

用法（在项目根目录，.env 里要有 OPENROUTER_API_KEY）：
    python scripts/try_image.py                                # 默认 gpt-image-1-mini，文生图
    python scripts/try_image.py --ref some.png                 # 带参考图（图生图）
    python scripts/try_image.py --model gpt-image-2 --prompt "一只猫"

请求格式和 config/models.yaml 中该模型的 image_api / 路由一致（与正式运行走同一段代码）。
成功时把图片存到 --out（默认 try_image.<扩展名>），并打印费用与 token 数；失败时打印错误并以
退出码 1 结束。
"""

from __future__ import annotations

import argparse
import asyncio
import mimetypes
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from roundtable.core.config import load_config
from roundtable.core.providers import Media, ProviderError, Secret
from roundtable.core.providers.openai_compat import OpenAICompatProvider

ROOT = Path(__file__).resolve().parent.parent
CHANNEL = "openrouter"
EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}


async def run(args: argparse.Namespace) -> int:
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
    refs = []
    if args.ref:
        path = Path(args.ref)
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        refs.append(Media("image", mime, path.read_bytes(), path.name))

    api = model.image_api_for(route)
    print(f"模型 {route.model}，接口 {api}，参考图 {len(refs)} 张 ...")
    provider = OpenAICompatProvider(CHANNEL, spec, Secret(key), timeout_s=180)
    try:
        out = await provider.generate_image(
            route.model, args.prompt, model.params_for(route), images=refs, api=api
        )
    except ProviderError as exc:
        print(f"失败：{exc.kind.value} {exc}", file=sys.stderr)
        return 1
    finally:
        await provider.aclose()

    image = out.images[0]
    target = Path(args.out or f"try_image.{EXT.get(image.mime, 'png')}")
    target.write_bytes(image.data)
    print(f"成功：{target}（{image.mime}，{len(image.data)} 字节）")
    print(f"费用 {out.cost_usd} 美元，输入 {out.input_tokens} / 输出 {out.output_tokens} token")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="gpt-image-1-mini", help="models.yaml 中的模型 id")
    parser.add_argument("--prompt", default="一只坐在窗台上的橘猫，日系插画风格")
    parser.add_argument("--ref", help="参考图文件（可选）")
    parser.add_argument("--out", help="输出文件（默认 try_image.<扩展名>）")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
