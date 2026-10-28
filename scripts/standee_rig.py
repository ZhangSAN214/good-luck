"""把一张大立绘做成「网格形变」用的素材：分离瞳孔、补眼白，生成全身各部位的形变权重图。

不调用任何模型、不花钱，只在本机处理图片。需要 numpy 和 pillow（pip install numpy pillow）。

用法（项目根目录）：
    python scripts/standee_rig.py data/avatars/_codex/claude_standee/rig_D.json

配置文件（JSON，坐标都是原图像素）写明：
- image：立绘图（相对配置文件），out：输出目录名（相对配置文件，已存在的文件会覆盖）
- eyes：每只眼睛的眼眶开口多边形 opening 和瞳孔椭圆 iris [中心x, 中心y, 半宽, 半高]
- pivots：neck（转头支点）、face（脸部中心，转头时五官比轮廓移得多）、elbow（拿书手臂的支点）、
  waist（呼吸的下沿）、feet（全身摇摆的支点，可在图外）
- regions：各部位的范围（多边形 poly / 椭圆 ellipse / 高斯 gauss，feather 为羽化像素）。
  头发和饰品写 root、tip（发根、发梢），越靠发梢摆得越多；hair=true 时只让头发和背景跟着摆，
  压在衣服上的部分摆得少，免得把衣服一起扭歪；smooth 为抹平半径（默认 14 像素）。
  hand（可选）：贴着脸的手（如托腮），整只手按 follow（默认 0.5）的比例跟着头走，不被拉扯变形。
  裙摆写 top、bottom（上下沿），从 top 往下 ramp 像素（默认 220）逐渐放开摆动。

输出（都在 out 目录里，前端按 rig.json 读取）：
- base.png：瞳孔挖掉、补好眼白的立绘（纯色底保留，前端照常抠底）
- iris.png：瞳孔层（透明底，往眼睑下面多延伸了一圈，转眼时不露白边）
- w1.png：R 头 / G 脸部纵深 / B 胸口 / A 拿书手臂
- w2.png：R 左侧长发 / G 右侧长发 / B 前发与胸前发丝 / A 裙摆
- w3.png：R 饰品（发带、流苏、蝴蝶结）/ G 沿发丝的位置（0 发根～1 发梢）/ B 眼眶开口
- rig.json：支点、尺寸、文件名，抄进 standee.json 的 rig 字段即可
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:  # pragma: no cover - 只在缺依赖时提示
    sys.exit("需要 numpy 和 pillow：pip install numpy pillow")

SS = 4  # 画多边形时的超采样倍数（边缘抗锯齿）
WEIGHT_FILES = ("w1.png", "w2.png", "w3.png")


def poly_mask(size: tuple[int, int], pts, feather: float = 0) -> np.ndarray:
    """多边形 → 0～1 浮点蒙版；feather>0 时高斯羽化。"""
    w, h = size
    m = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(m).polygon([(x * SS, y * SS) for x, y in pts], fill=255)
    m = m.resize((w, h), Image.Resampling.BOX)
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather / 2))
    return np.asarray(m, dtype=np.float32) / 255


def ellipse_mask(size, cx, cy, rx, ry, feather: float = 0) -> np.ndarray:
    w, h = size
    m = Image.new("L", (w * SS, h * SS), 0)
    ImageDraw.Draw(m).ellipse(
        [(cx - rx) * SS, (cy - ry) * SS, (cx + rx) * SS, (cy + ry) * SS], fill=255
    )
    m = m.resize((w, h), Image.Resampling.BOX)
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather / 2))
    return np.asarray(m, dtype=np.float32) / 255


def blur(a: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    return np.asarray(img.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255


def smoothstep(a: float, b: float, v: np.ndarray) -> np.ndarray:
    t = np.clip((v - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def border_color(rgb: np.ndarray) -> np.ndarray:
    """四条边上最常见的颜色（纯色底）。"""
    edge = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]]).astype(np.int32)
    keys = (edge[:, 0] >> 3) << 10 | (edge[:, 1] >> 3) << 5 | (edge[:, 2] >> 3)
    best = np.bincount(keys).argmax()
    return edge[keys == best].mean(axis=0)


def diffuse_fill(
    rgb: np.ndarray, known: np.ndarray, region: np.ndarray, iters: int = 400
) -> np.ndarray:
    """region 里的像素用 known 像素向内扩散补上（拉普拉斯平滑），只在小范围内用。"""
    ys, xs = np.nonzero(region)
    if not len(ys):
        return rgb
    y0, y1 = max(ys.min() - 3, 0), min(ys.max() + 4, rgb.shape[0])
    x0, x1 = max(xs.min() - 3, 0), min(xs.max() + 4, rgb.shape[1])
    sub = rgb[y0:y1, x0:x1].astype(np.float32).copy()
    kn = known[y0:y1, x0:x1]
    if not kn.any():
        return rgb
    # 不是已知像素的一律参与扩散（不让睫毛之类的深色把补色拉暗），只写回 region
    free = ~kn
    sub[free] = sub[kn].mean(axis=0)  # 先用已知像素的平均色打底，收敛更快
    for _ in range(iters):
        p = np.pad(sub, ((1, 1), (1, 1), (0, 0)), mode="edge")
        avg = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) / 4
        sub[free] = avg[free]
    out = rgb.copy()
    todo = region[y0:y1, x0:x1] & free
    out[y0:y1, x0:x1][todo] = np.clip(sub[todo], 0, 255).astype(rgb.dtype)
    return out


def along(size, root, tip) -> np.ndarray:
    """每个像素在发根→发梢方向上的位置（0～1）。"""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = tip[0] - root[0], tip[1] - root[1]
    n = dx * dx + dy * dy or 1.0
    return np.clip(((xx - root[0]) * dx + (yy - root[1]) * dy) / n, 0, 1)


def split_eyes(rgb: np.ndarray, eyes: list[dict]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """返回 (挖掉瞳孔补好眼白的图, 瞳孔层 RGBA, 眼眶开口蒙版)。"""
    h, w = rgb.shape[:2]
    size = (w, h)
    base = rgb.copy()
    iris = np.zeros((h, w, 4), dtype=np.uint8)
    opening = np.zeros((h, w), dtype=np.float32)
    for eye in eyes:
        cx, cy, rx, ry = eye["iris"]
        o = poly_mask(size, eye["opening"])
        e = ellipse_mask(size, cx, cy, rx, ry)
        opening = np.maximum(opening, o)
        ob, eb = o > 0.5, e > 0.02
        # 瞳孔层：眼眶内的瞳孔像素照抄，被眼睑盖住的部分从瞳孔向外扩散补上（转眼时不露白边）
        big = ellipse_mask(size, cx, cy, rx + 2, ry + 2) > 0.02
        layer = diffuse_fill(rgb, ob & eb, big, iters=300)
        iris[big, :3] = layer[big]
        iris[..., 3] = np.maximum(
            iris[..., 3], (ellipse_mask(size, cx, cy, rx + 1, ry + 1, 2) * 255).astype(np.uint8)
        )
        # 底图：眼眶内瞳孔的位置用周围的眼白补上
        light = rgb.astype(np.float32).mean(-1) > 170  # 只拿眼白（亮的像素）当补色来源，不混进睫毛
        known = ob & light & ~(ellipse_mask(size, cx, cy, rx + 1.5, ry + 1.5) > 0.02)
        base = diffuse_fill(base, known, ob & big, iters=500)
    return base, iris, opening


def build(config_path: Path) -> Path:
    cfg = json.loads(config_path.read_text(encoding="utf-8-sig"))
    here = config_path.parent
    img = Image.open(here / cfg["image"]).convert("RGB")
    rgb = np.asarray(img).copy()
    h, w = rgb.shape[:2]
    size = (w, h)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    reg = cfg["regions"]

    # 不透明（不是底色）、头发色
    bg = border_color(rgb)
    alpha = smoothstep(14, 40, np.sqrt(((rgb.astype(np.float32) - bg) ** 2).sum(-1)))
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)
    hue, sat, val = hsv[..., 0] * 360 / 255, hsv[..., 1] / 255, hsv[..., 2] / 255
    hair = ((hue > 6) & (hue < 40) & (sat > 0.45) & (val > 0.28)).astype(np.float32)
    # 衣服、皮肤等「不是头发的实体」：头发层在这里少摆
    body = blur(np.clip(alpha - blur(hair, 1.5) * 1.4, 0, 1), 5)

    def region(r: dict) -> np.ndarray:
        if "ellipse" in r:
            m = ellipse_mask(size, *r["ellipse"], r.get("feather", 0))
        elif "gauss" in r:
            cx, cy, sx, sy = r["gauss"]
            m = np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2) / 2)
        else:
            m = poly_mask(size, r["poly"], r.get("feather", 0))
        if "fade_y" in r:
            m = m * (1 - smoothstep(*r["fade_y"], yy))
        if r.get("hair"):
            # 压在衣服上的地方少摆；再整体抹平，形变场不能有陡坎（否则发丝会被撕开）
            m = blur(m * np.clip(1 - body, 0, 1), r.get("smooth", 14))
        return m.astype(np.float32)

    head = region(reg["head"])
    if "hand" in reg:
        # 托腮的手：整只手按同一个比例跟着头走（像一块整体移动），不在手指和手腕之间被拉扯变形
        hand = region(reg["hand"])
        head = head * (1 - hand) + reg["hand"].get("follow", 0.5) * hand
    face = region(reg["face"]) * head
    chest = region(reg["chest"])
    arm = region(reg["arm"])
    hl, hr, front = region(reg["hair_l"]), region(reg["hair_r"]), region(reg["front"])
    sk = reg["skirt"]
    skirt = region(sk) * smoothstep(sk["top"], sk["top"] + sk.get("ramp", 220), yy)
    # 头上的头发离脸越远越不跟着头转（长发由各自的弹簧带）
    hl *= 1 - head * 0.6
    hr *= 1 - head * 0.6
    acc = np.zeros((h, w), dtype=np.float32)
    s_num = np.zeros((h, w), dtype=np.float32)
    s_den = np.full((h, w), 1e-4, dtype=np.float32)
    for name, m in (("hair_l", hl), ("hair_r", hr), ("front", front)):
        s_num += m * along(size, reg[name]["root"], reg[name]["tip"])
        s_den += m
    for part in reg["acc"]["parts"]:
        m = poly_mask(size, part["poly"], reg["acc"].get("feather", 0))
        acc = np.maximum(acc, m)
        s_num += m * along(size, part["root"], part["tip"])
        s_den += m
    s = s_num / s_den

    base, iris, opening = split_eyes(rgb, cfg.get("eyes", []))

    out = here / cfg.get("out", "rig")
    out.mkdir(parents=True, exist_ok=True)

    def save4(name: str, chans) -> None:
        arr = np.stack([np.clip(c, 0, 1) for c in chans], -1)
        Image.fromarray((arr * 255 + 0.5).astype(np.uint8), "RGBA").save(out / name, optimize=True)

    zero = np.zeros((h, w), dtype=np.float32)
    save4("w1.png", (head, face, chest, arm))
    save4("w2.png", (hl, hr, front, skirt))
    save4("w3.png", (acc, s, opening, zero + 1))
    Image.fromarray(base).save(out / "base.png", optimize=True)
    Image.fromarray(iris, "RGBA").save(out / "iris.png", optimize=True)
    rel = out.relative_to(here).as_posix()
    rig = {
        "base": f"{rel}/base.png",
        "iris": f"{rel}/iris.png",
        "weights": [f"{rel}/{n}" for n in WEIGHT_FILES],
        "size": [w, h],
        "pivots": cfg["pivots"],
        "skirt": [sk["top"], sk["bottom"]],
    }
    (out / "rig.json").write_text(json.dumps(rig, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成大立绘的网格形变素材（瞳孔层、权重图）")
    ap.add_argument("config", type=Path, help="rig 配置 JSON")
    args = ap.parse_args(argv)
    out = build(args.config)
    print(f"已生成：{out}（把 rig.json 的内容写进 standee.json 对应立绘的 rig 字段）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
