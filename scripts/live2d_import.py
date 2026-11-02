"""把拆好的立绘图层（layers.json + layers/*.png）转换给两个开源 Live2D 自动绑定工具，并调用它们。

不调用任何模型、不花钱，只在本机处理图片。需要 numpy 和 pillow。两个工具都只当外部程序调用，
本脚本不包含它们的代码（PSD2Live 是 GPL-3.0，image2live2d 是 Apache-2.0）。

用法（项目根目录；素材和生成的模型都不进开发分支，放在 assets/claude-live2d 分支或临时目录）：

    # 1. 合并效果层、按两个工具的命名规范各出一份输入
    python scripts/live2d_import.py prepare 素材/hd_v3 工作目录 [--i2l-scale 0.5]

    # 2. image2live2d：在装了它的 Python 里跑（.moc3 套件 + .cmo3）
    python scripts/live2d_import.py image2live2d 工作目录 输出目录 --python 工具的python

    # 3. PSD2Live：命令行生成（默认预设）
    python scripts/live2d_import.py psd2live 工作目录 输出目录 --exe psd2live可执行文件
    # 3'. PSD2Live「完整」预设：先起它的无窗口 MCP 服务（psd2live mcp），环境变量
    #     PSD2LIVE_MCP_ENDPOINT / PSD2LIVE_MCP_TOKEN 指向它；--demo 顺便导出 18 秒演示 MP4
    python scripts/live2d_import.py psd2live-full 工作目录 输出目录 --mcp-proxy 它的源码目录 --demo

prepare 的输出：
- parts/*.png、parts.json：每个部件层合并了贴在它上面的阴影 / 高光 / 边缘光
  （按 layers.json 的混合模式，只画在部件本身不透明的地方），表情层原样保留；
- i2l_layers/：image2live2d 要的「{顺序}_{角色}.png」整画布图层；
- psd2live.psd：PSD2Live 要的分层 PSD，图层名按它的识别词表改写（嘴给最大张口原画，闭嘴线另一层）。

左右约定：素材里 l 是画面右边（角色自己的左边），两个工具都是同一约定，不用翻转。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# 部件层名去掉序号和左右后的「词根」→ image2live2d 的语义角色。没列出的词根会报错，免得悄悄丢层。
I2L_ROLES = {
    "back hair": "hair_back",
    "bangs": "hair_front",
    "side hair": "hair_side",
    "face": "face_base",
    "eyebrow": "eyebrow",
    "eyewhite": "eye_white",
    "irides": "pupil",
    "eyelash": "eye",
    "eye_closed": "eye_closed",
    "nose": "nose",
    "mouth": "mouth",
    "ears": "ear",
    "neck": "neck",
    "topwear": "torso",
    "handwear": "arm",
    "cuff": "arm",
    "collar ruffle": "clothing",
    "jabot": "clothing",
    "cravat": "clothing",
    "brooch": "clothing",
    "inner skirt": "clothing",
    "overskirt": "clothing",
    "skirt hem ruffle": "clothing",
    "skirt bow": "accessory",
    "footwear": "clothing",
    "earwear": "accessory",
    "headwear": "accessory",
    "hair flower": "accessory",
}
# 合并进别的部件再给 image2live2d（泪痣很淡、跟着脸走；单独一层会被当成第二张脸）
I2L_MERGE_INTO = {"beauty mark": "face"}
# 带左右的角色（加 _l / _r 后缀）
I2L_SIDED = {"eyebrow", "eye_white", "pupil", "eye", "eye_closed", "ear", "arm"}
# image2live2d 自己合成张嘴的口腔，整张的张嘴表情层不给它
I2L_SKIP = {"mouth_open"}

# 词根 → PSD2Live 的图层名（它按名字识别部位，见它的 docs/zh/spec/PSD_LAYER_SPEC.md）。
# 侧发在它的词表里属于前发；「skirt xxx」「neckwear xxx」按前缀归到裙子、领饰。
P2L_NAMES = {
    "back hair": "back hair",
    "bangs": "bangs",
    "side hair": "front hair side",
    "face": "face",
    "beauty mark": "facedetail mole",
    "eyebrow": "eyebrow",
    "eyewhite": "eyewhite",
    "irides": "irides",
    "eyelash": "eyelash",
    "eye_closed": "eye close",
    "nose": "nose",
    "mouth": "mouth close",  # 素材的嘴是闭嘴线
    "mouth_open": "mouth",  # 它要的是「张到最大」的嘴，闭合靠变形
    "ears": "ears",
    "neck": "neck",
    "topwear": "topwear",
    "handwear": "handwear",
    "cuff": "handwear cuff",
    "collar ruffle": "collar",
    "jabot": "neckwear jabot",
    "cravat": "neckwear cravat",
    "brooch": "neckwear brooch",
    "inner skirt": "skirt inner",
    "overskirt": "skirt over",
    "skirt hem ruffle": "skirt hem",
    "skirt bow": "skirt bow",
    "footwear": "footwear",
    "earwear": "earwear",
    "headwear": "headwear",
    "hair flower": "headwear flower",
}
# PSD2Live 只要一种最大张口，「哦」口型由它的口型参数变形出来
P2L_SKIP_VARIANTS = {("mouth_open", "o")}
P2L_PLAIN = {("mouth_open", "a")}  # 唯一保留的那种张嘴，不加后缀

_NAME = re.compile(r"^(?P<stem>.+?)(?:-(?P<side>[lr]))?(?:-(?P<variant>[0-9a-z]+))?$")


@dataclass
class Part:
    """一个部件层（效果层已合并进来）。bbox 是画布像素 [左, 上, 右, 下]。"""

    name: str
    order: int
    bbox: tuple[int, int, int, int]
    kind: str = "part"  # part / expression
    visible: bool = True
    replaces: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)
    file: str = ""


def split_name(name: str) -> tuple[str, str | None, str | None]:
    """'side hair-l-2' → ('side hair', 'l', '2')；'mouth_open-a' → ('mouth_open', None, 'a')；
    'bangs-3' → ('bangs', None, '3')。"""
    m = _NAME.match(name.strip())
    stem, side, variant = m.group("stem"), m.group("side"), m.group("variant")
    if side is None and variant in ("l", "r"):
        side, variant = variant, None
    return stem, side, variant


def i2l_role(name: str) -> str | None:
    """部件名 → image2live2d 角色（None = 不给它）。"""
    stem, side, _ = split_name(name)
    if stem in I2L_SKIP or stem in I2L_MERGE_INTO:
        return None
    if stem not in I2L_ROLES:
        raise KeyError(f"image2live2d 没有对应角色的图层：{name!r}（在 I2L_ROLES 里补上）")
    role = I2L_ROLES[stem]
    if role in I2L_SIDED:
        role = f"{role}_{side or 'l'}"
    return role


def p2l_name(name: str) -> str | None:
    """部件名 → PSD2Live 图层名（None = 不给它）。序号放最后，左右用「-l / -r」。"""
    stem, side, variant = split_name(name)
    if (stem, variant) in P2L_SKIP_VARIANTS:
        return None
    if stem not in P2L_NAMES:
        raise KeyError(f"PSD2Live 没有对应名称的图层：{name!r}（在 P2L_NAMES 里补上）")
    out = P2L_NAMES[stem]
    if side:
        out += f"-{side}"
    if variant and (stem, variant) not in P2L_PLAIN:
        out += f" {variant}"  # 数字是同类第几块；upper / lower 这类词它当作自己的标签
    return out


# ---------------------------------------------------------------------------------------------
# 合并效果层
# ---------------------------------------------------------------------------------------------


def blend_into(base, effect, mode: str):
    """把 effect（同尺寸 RGBA，0～1 浮点）按混合模式画到 base 上，只画在 base 不透明的地方
    （剪切蒙版）；返回新数组，base 的透明度不变。"""
    import numpy as np

    b, e = base[..., :3], effect[..., :3]
    a = effect[..., 3:4]
    if mode == "multiply":
        mixed = b * e
    elif mode == "screen":
        mixed = 1.0 - (1.0 - b) * (1.0 - e)
    elif mode == "normal":
        mixed = e
    else:
        raise ValueError(f"不支持的混合模式：{mode}")
    out = base.copy()
    out[..., :3] = b * (1.0 - a) + mixed * a
    return np.clip(out, 0.0, 1.0)


def load_spec(src: Path) -> list[dict]:
    spec = json.loads((src / "layers.json").read_text(encoding="utf-8"))
    layers = spec["layers"]
    names = {layer["name"] for layer in layers}
    for layer in layers:
        clip = layer.get("clip_to")
        if layer["type"] == "effect" and clip not in names:
            raise ValueError(f"效果层 {layer['name']!r} 的 clip_to {clip!r} 不存在")
    return sorted(layers, key=lambda layer: layer["order"])


def merge_parts(src: Path, out_dir: Path) -> list[Part]:
    """读 layers.json，把效果层合并进所属部件，部件按原包围盒裁好存到 out_dir/parts/。"""
    import numpy as np
    from PIL import Image

    layers = load_spec(src)
    effects: dict[str, list[dict]] = {}
    for layer in layers:
        if layer["type"] == "effect":
            effects.setdefault(layer["clip_to"], []).append(layer)

    parts_dir = out_dir / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Part] = []
    for layer in layers:
        if layer["type"] == "effect":
            continue
        x0, y0, x1, y1 = layer["bbox"]
        img = np.asarray(Image.open(src / layer["file"]).convert("RGBA"), dtype=np.float32) / 255.0
        if img.shape[:2] != (y1 - y0, x1 - x0):
            raise ValueError(f"{layer['file']} 尺寸与 bbox 不符")
        used = []
        for eff in effects.get(layer["name"], []):  # 已按 order 从下到上
            ex0, ey0, ex1, ey1 = eff["bbox"]
            canvas = np.zeros_like(img)
            eimg = (
                np.asarray(Image.open(src / eff["file"]).convert("RGBA"), dtype=np.float32) / 255.0
            )
            # 效果层的包围盒按理在部件之内；超出的部分本来就会被剪切掉
            cx0, cy0 = max(ex0, x0), max(ey0, y0)
            cx1, cy1 = min(ex1, x1), min(ey1, y1)
            if cx0 < cx1 and cy0 < cy1:
                canvas[cy0 - y0 : cy1 - y0, cx0 - x0 : cx1 - x0] = eimg[
                    cy0 - ey0 : cy1 - ey0, cx0 - ex0 : cx1 - ex0
                ]
                img = blend_into(img, canvas, eff["blend_mode"])
            used.append(eff["name"])
        part = Part(
            name=layer["name"],
            order=layer["order"],
            bbox=(x0, y0, x1, y1),
            kind="expression" if layer["type"] == "expression" else "part",
            visible=layer.get("visible", True),
            replaces=list(layer.get("replaces", [])),
            effects=used,
            file=f"parts/{layer['order']:03d}_{re.sub(r'[^0-9A-Za-z_-]+', '_', layer['name'])}.png",
        )
        Image.fromarray((img * 255.0 + 0.5).astype(np.uint8), "RGBA").save(out_dir / part.file)
        parts.append(part)
    return parts


# ---------------------------------------------------------------------------------------------
# 写给两个工具的输入
# ---------------------------------------------------------------------------------------------


def write_i2l_layers(parts: list[Part], work: Path, canvas: tuple[int, int], scale: float) -> Path:
    """image2live2d 的输入：整画布、从下到上编号的「{顺序}_{角色}.png」。"""
    from PIL import Image

    out = work / "i2l_layers"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    w, h = round(canvas[0] * scale), round(canvas[1] * scale)
    by_name = {p.name: p for p in parts}
    extras: dict[str, list[Part]] = {}
    for part in parts:
        target = I2L_MERGE_INTO.get(split_name(part.name)[0])
        if target:
            extras.setdefault(target, []).append(part)
    n = 0
    for part in parts:
        role = i2l_role(part.name)
        if role is None:
            continue
        img = Image.open(work / part.file).convert("RGBA")
        x0, y0, x1, y1 = part.bbox
        for extra in extras.get(part.name, []):
            if extra.name in by_name:
                img.alpha_composite(
                    Image.open(work / extra.file).convert("RGBA"),
                    (extra.bbox[0] - x0, extra.bbox[1] - y0),
                )
        if scale != 1.0:
            size = (max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale)))
            img = img.resize(size, Image.LANCZOS)
        full = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        full.paste(img, (round(x0 * scale), round(y0 * scale)))
        full.save(out / f"{n:03d}_{role}.png")
        n += 1
    return out


def psd_bytes(
    width: int, height: int, layers: list[tuple[str, tuple[int, int], object, bool]]
) -> bytes:
    """最小的 8 位 RGB 分层 PSD（无压缩）。

    layers：(名字, (左, 上), RGBA 的 PIL 图, 是否可见)，从下到上。"""
    import numpy as np
    from PIL import Image

    def pascal(name: str) -> bytes:
        raw = name.encode("ascii", "replace")[:255]
        data = bytes([len(raw)]) + raw
        return data + b"\0" * (-len(data) % 4)

    def unicode_name(name: str) -> bytes:
        text = name.encode("utf-16-be")
        body = struct.pack(">I", len(name)) + text
        body += b"\0" * (-len(body) % 4)
        return b"8BIMluni" + struct.pack(">I", len(body)) + body

    records, channel_data = b"", b""
    composite = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for name, (left, top), img, visible in layers:
        arr = np.asarray(img.convert("RGBA"))
        h, w = arr.shape[:2]
        planes = [arr[..., 3], arr[..., 0], arr[..., 1], arr[..., 2]]
        ids = [-1, 0, 1, 2]
        rec = struct.pack(">iiiiH", top, left, top + h, left + w, 4)
        for cid in ids:
            rec += struct.pack(">hI", cid, 2 + w * h)
        flags = 0 if visible else 2
        extra = struct.pack(">I", 0) + struct.pack(">I", 0) + pascal(name) + unicode_name(name)
        rec += b"8BIMnorm" + struct.pack(">BBBB", 255, 0, flags, 0) + struct.pack(">I", len(extra))
        records += rec + extra
        for plane in planes:
            channel_data += struct.pack(">H", 0) + np.ascontiguousarray(plane).tobytes()
        if visible:
            composite.alpha_composite(img.convert("RGBA"), (left, top))
    info = struct.pack(">h", -len(layers)) + records + channel_data
    info += b"\0" * (len(info) % 2)
    layer_info = struct.pack(">I", len(info)) + info
    lmi = layer_info + struct.pack(">I", 0)
    comp = np.asarray(composite)
    image = struct.pack(">H", 0) + b"".join(
        np.ascontiguousarray(comp[..., c]).tobytes() for c in (0, 1, 2, 3)
    )
    header = b"8BPS" + struct.pack(">H6xHIIHH", 1, 4, height, width, 8, 3)
    return (
        header
        + struct.pack(">I", 0)
        + struct.pack(">I", 0)
        + struct.pack(">I", len(lmi))
        + lmi
        + image
    )


def write_p2l_psd(parts: list[Part], work: Path, canvas: tuple[int, int]) -> Path:
    """PSD2Live 的输入：按它的词表命名的分层 PSD。表情层也设成可见，由它的识别结果决定用法。"""
    from PIL import Image

    layers = []
    for part in parts:
        name = p2l_name(part.name)
        if name is None:
            continue
        layers.append((name, part.bbox[:2], Image.open(work / part.file), True))
    path = work / "psd2live.psd"
    path.write_bytes(psd_bytes(canvas[0], canvas[1], layers))
    return path


def prepare(src: Path, work: Path, i2l_scale: float = 1.0) -> dict:
    spec = json.loads((src / "layers.json").read_text(encoding="utf-8"))
    canvas = tuple(spec["canvas"])
    work.mkdir(parents=True, exist_ok=True)
    parts = merge_parts(src, work)
    (work / "parts.json").write_text(
        json.dumps(
            {"canvas": canvas, "parts": [p.__dict__ for p in parts]}, ensure_ascii=False, indent=1
        ),
        encoding="utf-8",
    )
    i2l = write_i2l_layers(parts, work, canvas, i2l_scale)
    psd = write_p2l_psd(parts, work, canvas)
    merged = sum(len(p.effects) for p in parts)
    return {"parts": len(parts), "effects_merged": merged, "i2l_layers": str(i2l), "psd": str(psd)}


# ---------------------------------------------------------------------------------------------
# 调用两个工具
# ---------------------------------------------------------------------------------------------

# 在 image2live2d 的 Python 里执行：从图层目录建 rig，出 .moc3 套件（单张图集）和 .cmo3。
# 走的是它 tools/emit_cubism_bundle.py 的同一条公开流程（命令行的 --live2d 默认只写 JSON）。
_I2L_RUNNER = r"""
import json, sys
from pathlib import Path
from image2live2d.backends.live2d import cdi3, model3, motion3, physics3
from image2live2d.backends.live2d.cmo3 import rig_to_cmo3
from image2live2d.backends.live2d.moc3_binary import write_moc3
from image2live2d.backends.live2d.moc3_emit import build_atlas, rig_to_moc3
from image2live2d.core import decompose
from image2live2d.pipeline import rig_from_stack
from image2live2d.core.qa import evaluate

layers, out, name = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
rig = rig_from_stack(decompose.from_layer_dir(layers), name=name, source=str(layers))
out.mkdir(parents=True, exist_ok=True)
(out / "textures").mkdir(exist_ok=True)
atlas, uv = build_atlas(rig, layers)
atlas.save(out / "textures" / "texture_00.png")
(out / f"{name}.moc3").write_bytes(write_moc3(rig_to_moc3(rig, atlas_uv=uv)))
def wj(rel, doc):
    (out / rel).write_text(json.dumps(doc, indent=2))
phys = None
if rig.physics:
    phys = f"{name}.physics3.json"; wj(phys, physics3.physics3(rig))
wj(f"{name}.cdi3.json", cdi3.cdi3(rig))
motions = {}
(out / "motions").mkdir(exist_ok=True)
for anim in rig.animations:
    rel = f"motions/{anim.name}.motion3.json"; wj(rel, motion3.motion3(anim))
    motions.setdefault(anim.name.capitalize(), []).append(rel)
wj(f"{name}.model3.json", model3.model3(
    rig, moc=f"{name}.moc3", textures=["textures/texture_00.png"],
    physics=phys, display_info=f"{name}.cdi3.json", motions=motions or None))
(out.parent / f"{name}.cmo3").write_bytes(rig_to_cmo3(rig, asset_root=layers))
qa = evaluate(rig, name)
print(json.dumps({
    "parameters": [p.id for p in rig.parameters], "physics": len(rig.physics),
    "meshes": len(rig.meshes), "motions": sorted(motions), "qa_passed": bool(qa.passed)}))
"""


def run_image2live2d(work: Path, out: Path, python: str, name: str = "model") -> dict:
    """在 image2live2d 的 Python 里出模型：out/<name>.model3.json 等，out/../<name>.cmo3。"""
    res = subprocess.run(
        [python, "-c", _I2L_RUNNER, str(work / "i2l_layers"), str(out), name],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"image2live2d 失败：{res.stderr[-2000:]}")
    return json.loads(res.stdout.strip().splitlines()[-1])


def psd2live_cli_args(psd: Path, out: Path, atlas: int = 4096) -> list[str]:
    """PSD2Live 命令行生成的参数（默认预设；导出 Cubism 5.0 的 .moc3 与 .cmo3）。"""
    return ["--input", str(psd), "--output", str(out), "--atlas", str(atlas)]


def run_psd2live(work: Path, out: Path, exe: str) -> list[str]:
    res = subprocess.run(
        [exe, *psd2live_cli_args(work / "psd2live.psd", out)],
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"PSD2Live 失败：{(res.stderr or res.stdout)[-2000:]}")
    return [line.strip() for line in res.stdout.splitlines() if line.strip().startswith("/")]


# ---------------------------------------------------------------------------------------------
# PSD2Live「完整」预设（走它的无窗口 MCP 服务）与共用的演示时间线
# ---------------------------------------------------------------------------------------------

# 界面上「完整」预设 = 默认预设 + 五官位移 + 前发 / 后发改为模拟；衣服模拟默认就开
FULL_PRESET_STEPS = ["front_hair", "back_hair", "clothing"]
DEMO_SECONDS = 18.0
DEMO_FPS = 30


def _smooth(a: float, b: float, t0: float, t1: float, t: float) -> float:
    if t <= t0:
        return a
    if t >= t1:
        return b
    u = (t - t0) / (t1 - t0)
    return a + (b - a) * u * u * (3 - 2 * u)


def _keys(points: list[tuple[float, float]], t: float) -> float:
    for (t0, a), (t1, b) in zip(points, points[1:], strict=False):
        if t0 <= t <= t1:
            return _smooth(a, b, t0, t1, t)
    return points[0][1] if t < points[0][0] else points[-1][1]


def demo_frame(t: float) -> dict[str, float]:
    """演示时间线（标准参数）：看左 → 看右 → 点头 → 说话 → 身体摆动 → 笑。视线比头早约 0.25 秒。
    两个工具的演示都吃这一份数据，对比才公平。"""
    p: dict[str, float] = {}
    gaze = [(0, 0), (2.0, 0), (2.4, -1), (4.8, -1), (5.1, 1), (7.6, 1), (8.0, 0), (18, 0)]
    head = [(0, 0), (2.25, 0), (3.1, -30), (4.8, -30), (5.35, 0), (5.9, 30), (7.6, 30), (8.3, 0)]
    p["ParamEyeBallX"] = _keys(gaze, t)
    p["ParamAngleX"] = _keys(head + [(18, 0)], t) + 2.0 * math.sin(t * 0.9)
    nod = [(0, 0), (8.3, 0), (8.6, -12), (8.9, 6), (9.3, 0), (18, 0)]
    p["ParamAngleY"] = _keys(nod, t) + 1.5 * math.sin(t * 0.7 + 1)
    p["ParamEyeBallY"] = _keys([(0, 0), (8.3, 0), (8.6, -0.3), (9.3, 0), (18, 0)], t)
    tilt = [
        (0, 0),
        (5.5, 0),
        (6.4, 10),
        (7.6, 10),
        (8.3, 0),
        (14, 0),
        (14.6, -8),
        (16, -8),
        (17, 0),
    ]
    p["ParamAngleZ"] = _keys(tilt, t)
    talk = 0.0
    if 9.5 <= t <= 13.0:
        env = _smooth(0, 1, 9.5, 9.7, t) * _smooth(1, 0, 12.8, 13.0, t)
        syl = abs(math.sin((t - 9.5) * math.pi * 3.1)) * (0.65 + 0.35 * math.sin((t - 9.5) * 5.3))
        talk = env * syl
    p["ParamMouthOpenY"] = talk
    form = [(0, 0.2), (9.5, 0.2), (10.5, -0.3), (11.5, 0.6), (13, 0.3), (16.3, 0.3), (16.8, 1)]
    p["ParamMouthForm"] = _keys(form + [(18, 1)], t)
    sway = 0.0
    if 13.0 <= t <= 16.2:
        env = _smooth(0, 1, 13, 13.3, t) * _smooth(1, 0, 15.6, 16.2, t)
        sway = 10 * math.sin((t - 13.0) * math.pi * 1.25) * env
    p["ParamBodyAngleX"] = sway + 0.8 * math.sin(t * 0.8)
    p["ParamBodyAngleZ"] = 0.6 * sway + 1.0 * math.sin(t * 0.55)
    p["ParamBreath"] = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 3.8)
    eye = 1.0
    for b in (1.3, 4.6, 7.9, 10.6, 12.9, 16.4):
        d = t - b
        if 0 <= d < 0.08:
            eye = min(eye, 1 - d / 0.08)
        elif 0.08 <= d < 0.22:
            eye = min(eye, (d - 0.08) / 0.14)
    smile = _keys([(0, 0), (16.3, 0), (16.8, 1), (18, 1)], t)
    p["ParamEyeLOpen"] = p["ParamEyeROpen"] = eye * (1 - 0.35 * smile)
    p["ParamEyeLSmile"] = p["ParamEyeRSmile"] = p["ParamCheek"] = smile
    p["ParamBrowLY"] = p["ParamBrowRY"] = 0.3 * smile + (0.2 if talk > 0.5 else 0.0)
    return p


def demo_clip(ranges: dict[str, tuple[float, float]], key_fps: int = 15) -> dict:
    """演示时间线 → PSD2Live 的动作片段（线性关键帧，按模型的参数范围截断，模型没有的参数跳过）。"""
    curves: dict[str, list[dict]] = {}
    n = int(DEMO_SECONDS * key_fps)
    for i in range(n + 1):
        t = min(i / key_fps, DEMO_SECONDS)
        for pid, v in demo_frame(t).items():
            if pid not in ranges:
                continue
            lo, hi = ranges[pid]
            key = {"time": round(t, 4), "value": round(min(hi, max(lo, v)), 4)}
            curves.setdefault(pid, []).append(key | {"interpolation": "LINEAR"})
    return {
        "id": "Demo",
        "name": "Demo",
        "loop": False,
        "duration": DEMO_SECONDS,
        "fps": DEMO_FPS,
        "curves": [{"parameter": p, "keys": k} for p, k in curves.items()],
    }


def run_psd2live_full(work: Path, out: Path, proxy_dir: Path, demo: bool = False) -> dict:
    """经 PSD2Live 的 MCP 服务：导入 PSD → 套「完整」预设 → 存工程 → 导出 .moc3 套件与 .cmo3
    （→ 演示 MP4）。mcp_proxy.py 是它自带的客户端，只在运行时导入，不复制进仓库。"""
    sys.path.insert(0, str(proxy_dir))
    from mcp_proxy import Client  # noqa: PLC0415

    out.mkdir(parents=True, exist_ok=True)
    report: dict = {}
    with Client(timeout=1800) as c:
        c.call(
            "project_import_psd",
            {
                "path": str((work / "psd2live.psd").resolve()),
                "discard_unsaved": True,
                "state": c.state(),
            },
        )
        c.edit("settings_update", changes={"featureDisplacementEnabled": True})
        for preset in FULL_PRESET_STEPS:
            r = c.edit("model_apply_preset", preset=preset)
            report[preset] = r.get("simulations", [])
        c.call(
            "project_save_as", {"path": str((out / "model.psd2live").resolve()), "state": c.state()}
        )
        r, _ = c.call(
            "project_export_model", {"state": c.state(), "output_directory": str(out.resolve())}
        )
        report["files"] = [f["path"] for f in r.get("files", [])]
        if demo:
            ranges, off = {}, 0
            while True:
                r, _ = c.call(
                    "workspace_inspect", {"scope": "parameters", "offset": off, "limit": 64}
                )
                ranges |= {q["id"]: (q["min"], q["max"]) for q in r["items"]}
                if not r.get("next"):
                    break
                off = r["next"]
            c.edit("motion_put", clip=demo_clip(ranges))
            settings = {"clip": "Demo", "fps": str(DEMO_FPS), "size": "1280", "physics": "true"}
            r, _ = c.call(
                "project_export_target",
                {
                    "state": c.state(),
                    "target": "mp4",
                    "settings": settings | {"background": "FFF3EEE8"},
                    "output_directory": str((out / "demo").resolve()),
                },
            )
            report["demo"] = r.get("files", [])
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="立绘图层 → 两个 Live2D 自动绑定工具")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare", help="合并效果层，生成两个工具的输入")
    p.add_argument("src", type=Path, help="含 layers.json 的素材目录")
    p.add_argument("work", type=Path, help="工作目录")
    p.add_argument("--i2l-scale", type=float, default=1.0, help="给 image2live2d 的图层缩放")
    p = sub.add_parser("image2live2d", help="用 image2live2d 出模型")
    p.add_argument("work", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--python", required=True, help="装了 image2live2d 的 Python")
    p.add_argument("--name", default="model")
    p = sub.add_parser("psd2live", help="用 PSD2Live 命令行出模型（默认预设）")
    p.add_argument("work", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--exe", required=True, help="PSD2Live 的命令行启动脚本")
    p = sub.add_parser("psd2live-full", help="经 PSD2Live 的 MCP 服务套「完整」预设出模型")
    p.add_argument("work", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument(
        "--mcp-proxy", type=Path, required=True, help="含 mcp_proxy.py 的 PSD2Live 源码目录"
    )
    p.add_argument("--demo", action="store_true", help="同时导出 18 秒演示 MP4")
    args = ap.parse_args(argv)

    if args.cmd == "prepare":
        print(json.dumps(prepare(args.src, args.work, args.i2l_scale), ensure_ascii=False))
    elif args.cmd == "image2live2d":
        print(json.dumps(run_image2live2d(args.work, args.out, args.python, args.name)))
    elif args.cmd == "psd2live-full":
        report = run_psd2live_full(args.work, args.out, args.mcp_proxy, args.demo)
        print(json.dumps(report, ensure_ascii=False))
    else:
        for line in run_psd2live(args.work, args.out, args.exe):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
