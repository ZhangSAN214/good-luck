"""把出图的宿舍场景图 / 窗外天空图处理成网页用的素材。

素材在用户本机（data/ 不进 git）。场景图的窗玻璃涂成纯绿 #00FF00，本脚本把它抠成透明，
网页会在后面垫上按时段和天气变化的天空；顺手压缩成 webp。

用法（在项目根目录）：
    python scripts/room_art.py 出图.png --member claude  # → room_day.webp
    python scripts/room_art.py 夜景.png --member claude --night  # → room_night.webp
    python scripts/room_art.py 天空.png --sky night_rain  # 天空图，不抠绿
    python scripts/room_art.py 出图.png --out 某处/房间.webp --dir D:/素材

写好后在 members.json 里给该成员加 "room_day" / "room_night"（images 里）；天空图放好即生效。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image

from roundtable.core.members_art import SKY_PHASES, SKY_WEATHERS

DEFAULT_DIR = "data/avatars/roundtable_assets"
MAX_WIDTH = 2400


def key_green(im: Image.Image) -> tuple[Image.Image, float]:
    """纯绿（及其抗锯齿边）变透明，边缘去绿色溢出。返回 (RGBA 图, 抠掉的比例)。"""
    im = im.convert("RGBA")
    px = im.load()
    w, h = im.size
    keyed = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            spill = g - max(r, b)
            if spill <= 40:
                continue
            if g > 150 and spill > 120:
                px[x, y] = (0, 0, 0, 0)
                keyed += 1
                continue
            # 边缘：按绿得多少给半透明，并把绿压回去
            alpha = max(0, min(255, int(a * (1 - (spill - 40) / 120))))
            px[x, y] = (r, max(r, b), b, alpha)
    return im, keyed / (w * h)


def shrink(im: Image.Image) -> Image.Image:
    if im.width <= MAX_WIDTH:
        return im
    return im.resize((MAX_WIDTH, round(im.height * MAX_WIDTH / im.width)), Image.LANCZOS)


def process(src: Path, out: Path, *, key: bool = True) -> str:
    im = Image.open(src)
    note = ""
    if key:
        im, ratio = key_green(im)
        note = (
            f"，抠掉 {ratio:.1%}（窗玻璃）"
            if ratio
            else "，没有找到绿色窗玻璃（窗外不会跟着时间变）"
        )
    else:
        im = im.convert("RGB")
    im = shrink(im)
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "WEBP", quality=86, method=6)
    return f"{out}：{im.width}×{im.height}，{out.stat().st_size // 1024} KB{note}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="处理宿舍场景图 / 窗外天空图")
    ap.add_argument("src", type=Path)
    ap.add_argument("--dir", type=Path, default=Path(DEFAULT_DIR), help="素材目录")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--member", help="成员 id：写到 members/<id>/room_day.webp")
    group.add_argument("--sky", help="天空图：<时段>_<天气>，如 day_clear")
    group.add_argument("--out", type=Path, help="直接指定输出文件")
    ap.add_argument("--night", action="store_true", help="夜景版（room_night.webp）")
    a = ap.parse_args(argv)
    if not a.src.is_file():
        print(f"找不到 {a.src}", file=sys.stderr)
        return 1
    if a.sky:
        phase, _, weather = a.sky.partition("_")
        if phase not in SKY_PHASES or weather not in SKY_WEATHERS:
            print(
                f"--sky 要写成 时段_天气：时段 {SKY_PHASES}，天气 {SKY_WEATHERS}", file=sys.stderr
            )
            return 1
        print(process(a.src, a.dir / "sky" / f"{a.sky}.webp", key=False))
        return 0
    out = a.out or a.dir / "members" / a.member / (
        "room_night.webp" if a.night else "room_day.webp"
    )
    print(process(a.src, out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
