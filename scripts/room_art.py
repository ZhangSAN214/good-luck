"""把出图的宿舍场景图 / 窗外天空图处理成网页用的素材。

素材在用户本机（data/ 不进 git）。场景图的窗玻璃涂成纯绿 #00FF00，本脚本把它抠成透明，
网页会在后面垫上按时段和天气变化的天空；顺手压缩成 webp。

用法（在项目根目录）：
    python scripts/room_art.py 出图.png --member claude  # → room_day.webp
    python scripts/room_art.py 夜景.png --member claude --night  # → room_night.webp
    python scripts/room_art.py 天空.png --sky night_rain  # 天空图，不抠绿
    python scripts/room_art.py 出图.png --out 某处/房间.webp --dir D:/素材
    python scripts/room_art.py --install 房间包目录                    # 一次装好整包（见下）

写好后在 members.json 里给该成员加 "room_day" / "room_night"（images 里）；天空图放好即生效。

房间包：一个目录里放处理好的 webp 和 rooms.json，--install 把图复制到每位成员的素材文件夹，
并把 images.room_day / room_night 和 room（床、家具点位）写进 members.json
（先备份成 members.json.bak）；包里 sky/ 下的天空图一并装到素材目录的 sky/。rooms.json 的样子：
    {"克劳德": {"day": "克劳德.webp", "night": "克劳德_夜.webp",
                "room": {"bed": {...}, "hots": [...]}}, …}
键可以是成员的 id、name 或 nickname。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageFilter

from roundtable.core.members_art import SKY_PHASES, SKY_WEATHERS

DEFAULT_DIR = "data/avatars/roundtable_assets"
MAX_WIDTH = 2400


def key_green(im: Image.Image) -> tuple[Image.Image, float]:
    """纯绿窗玻璃（及其抗锯齿边）变透明，边缘去绿色溢出。返回 (RGBA 图, 抠掉的比例)。

    只抠成片的绿：先找纯绿像素，再做一次开运算去掉细碎的绿（地板上的绿色倒影、高光），
    半透明的边缘只在窗玻璃附近处理，别处的绿色溢出只压色、不透明。
    """
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    hard = Image.new("L", (w, h), 0)
    hp = hard.load()
    for y in range(h):
        for x in range(w):
            r, g, b, _ = px[x, y]
            if g > 150 and g - max(r, b) > 120:
                hp[x, y] = 255
    k = max(3, (min(w, h) // 120) | 1)  # 793 高的图约 7 像素
    glass = hard.filter(ImageFilter.MinFilter(k)).filter(ImageFilter.MaxFilter(k))
    near = glass.filter(ImageFilter.MaxFilter(5))
    gp, np_ = glass.load(), near.load()
    keyed = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            spill = g - max(r, b)
            if spill <= 40:
                continue
            if gp[x, y] and hp[x, y]:
                px[x, y] = (0, 0, 0, 0)
                keyed += 1
            elif np_[x, y]:
                # 窗玻璃边缘：按绿得多少给半透明，并把绿压回去
                alpha = max(0, min(255, int(a * (1 - (spill - 40) / 120))))
                px[x, y] = (r, max(r, b), b, alpha)
            else:
                px[x, y] = (r, max(r, b) + 20 if g > max(r, b) + 20 else g, b, a)
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


def _folder(entry: dict) -> str:
    """成员已有图片所在的文件夹（相对素材目录）；没有就用 members/<id>。"""
    for value in (entry.get("images") or {}).values():
        if isinstance(value, dict):
            value = next((v for v in value.values() if isinstance(v, str)), None)
        if isinstance(value, str) and "/" in value:
            return value.rsplit("/", 1)[0]
    return f"members/{entry['id']}"


def install(pack: Path, root: Path) -> list[str]:
    """把房间包装进素材目录，返回每位成员一行的结果。"""
    rooms = json.loads((pack / "rooms.json").read_text(encoding="utf-8"))
    path = root / "members.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for key, spec in rooms.items():
        entry = next(
            (m for m in data["members"] if key in (m.get("id"), m.get("name"), m.get("nickname"))),
            None,
        )
        if entry is None:
            out.append(f"{key}：members.json 里没有这位成员，跳过")
            continue
        folder = _folder(entry)
        images = entry.setdefault("images", {})
        for kind in ("day", "night"):
            if spec.get(kind):
                dst = f"{folder}/room_{kind}.webp"
                (root / dst).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(pack / spec[kind], root / dst)
                images[f"room_{kind}"] = dst
        if "room" in spec:
            entry["room"] = spec["room"]
        out.append(f"{key}：装到 {folder}")
    skies = sorted((pack / "sky").glob("*.webp")) if (pack / "sky").is_dir() else []
    for f in skies:
        (root / "sky").mkdir(exist_ok=True)
        shutil.copyfile(f, root / "sky" / f.name)
    if skies:
        out.append(f"窗外天空图：{len(skies)} 张装到 sky/")
    shutil.copyfile(path, path.with_name("members.json.bak"))
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="处理宿舍场景图 / 窗外天空图")
    ap.add_argument("src", type=Path, nargs="?")
    ap.add_argument("--dir", type=Path, default=Path(DEFAULT_DIR), help="素材目录")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--install", type=Path, help="房间包目录（含 rooms.json）")
    group.add_argument("--member", help="成员 id：写到 members/<id>/room_day.webp")
    group.add_argument("--sky", help="天空图：<时段>_<天气>，如 day_clear")
    group.add_argument("--out", type=Path, help="直接指定输出文件")
    ap.add_argument("--night", action="store_true", help="夜景版（room_night.webp）")
    a = ap.parse_args(argv)
    if a.install:
        if not (a.install / "rooms.json").is_file() or not (a.dir / "members.json").is_file():
            print("房间包里要有 rooms.json，素材目录里要有 members.json", file=sys.stderr)
            return 1
        print("\n".join(install(a.install, a.dir)))
        return 0
    if a.src is None or not a.src.is_file():
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
