#!/usr/bin/env python3
"""检查一位成员的小人差分素材，并生成 pet.json。只用 Pillow，不改动任何图片。

用法：
  python scripts/check_pet_art.py <素材目录> --member whale [--write] [--strict]

命名：动作帧 `<成员>_<动作>_k<序号>.png`（序号从 0 连续）；表情 `<成员>_face_<表情>.png`。
检查：画布 1254×1254、RGBA、透明度只有 0 / 255、有内容、脚底在画布约 92% 高度（允许 ±3%）、
各动作帧数不少于清单要求。通过（或 --write 且无错误）才写 `pet.json`。
退出码 0 = 无错误，1 = 有错误。警告（缺少的可选动作等）不影响退出码，--strict 时算错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from PIL import Image

CANVAS = (1254, 1254)
FOOT_RATIO = 0.92
FOOT_TOL = 0.03
FRAME = re.compile(r"^(?P<member>[a-z0-9]+)_(?P<action>[a-z_]+?)_k(?P<n>\d+)\.png$")
FACE = re.compile(r"^(?P<member>[a-z0-9]+)_face_(?P<name>[a-z]+)\.png$")

# 动作 -> (最少帧数, 默认 fps, 是否循环, 是否必须)。必须的缺了算错误，可选的缺了只警告
ACTIONS: dict[str, tuple[int, int, bool, bool]] = {
    "idle": (3, 3, True, True),
    "wave": (4, 6, False, True),
    "talk": (3, 6, True, True),
    "think": (4, 4, True, True),
    "type": (3, 6, True, True),
    "sleepy": (4, 3, False, True),
    "celebrate": (4, 8, False, True),
    "drag": (3, 6, True, True),
    "land": (3, 8, False, True),
    "walk": (4, 8, True, False),
    "jump": (4, 8, False, False),
    "sleep": (3, 3, True, False),
}
# 与 core/members_art.py 保持一致的表情名
EXPRESSIONS = (
    "happy",
    "smug",
    "thinking",
    "surprised",
    "confused",
    "shy",
    "angry",
    "sad",
    "sleepy",
)


def scan(
    folder: Path, member: str
) -> tuple[dict[str, dict[int, Path]], dict[str, Path], list[str]]:
    """按文件名归类。返回（动作 -> 序号 -> 路径，表情 -> 路径，命名错误）。"""
    actions: dict[str, dict[int, Path]] = {}
    faces: dict[str, Path] = {}
    errors: list[str] = []
    for p in sorted(folder.glob("*.png")):
        m = FRAME.match(p.name)
        f = FACE.match(p.name)
        if f and f["member"] == member:
            if f["name"] not in EXPRESSIONS:
                errors.append(f"{p.name}：未知表情 {f['name']}")
            else:
                faces[f["name"]] = p
        elif m and m["member"] == member:
            if m["action"] not in ACTIONS:
                errors.append(f"{p.name}：未知动作 {m['action']}")
            else:
                actions.setdefault(m["action"], {})[int(m["n"])] = p
        else:
            errors.append(
                f"{p.name}：命名不符（应为 {member}_动作_k0.png 或 {member}_face_表情.png）"
            )
    return actions, faces, errors


def check_image(path: Path, face: bool) -> list[str]:
    """单张图的问题列表。"""
    try:
        im = Image.open(path)
        im.load()
    except Exception as exc:  # 损坏的文件
        return [f"{path.name}：打不开（{type(exc).__name__}）"]
    if im.size != CANVAS:
        return [f"{path.name}：尺寸 {im.size[0]}×{im.size[1]}，应为 {CANVAS[0]}×{CANVAS[1]}"]
    alpha = im.convert("RGBA").getchannel("A")
    hist = alpha.histogram()
    semi = sum(hist[1:255])
    out: list[str] = []
    if sum(hist[1:]) == 0:
        return [f"{path.name}：整张全透明"]
    if semi:
        out.append(f"{path.name}：有 {semi} 个半透明像素（应只有 0 / 255）")
    if not face:
        box = alpha.point(lambda v: 255 if v >= 128 else 0).getbbox()
        if box is None:
            return out
        foot = box[3] / CANVAS[1]
        if abs(foot - FOOT_RATIO) > FOOT_TOL:
            out.append(
                f"{path.name}：脚底在画布 {foot:.1%} 处（应约 {FOOT_RATIO:.0%}±{FOOT_TOL:.0%}）"
            )
    return out


def check(folder: Path, member: str) -> tuple[list[str], list[str], dict]:
    """返回（错误，警告，pet.json 内容）。"""
    actions, faces, errors = scan(folder, member)
    warnings: list[str] = []
    pet_actions: dict[str, dict] = {}
    for name, (need, fps, loop, required) in ACTIONS.items():
        frames = actions.get(name, {})
        if not frames:
            (errors if required else warnings).append(f"缺少动作 {name}（至少 {need} 帧）")
            continue
        order = sorted(frames)
        if order != list(range(len(order))):
            errors.append(f"{name}：序号不连续 {order}（应从 k0 起连续）")
        if len(order) < need:
            errors.append(f"{name}：只有 {len(order)} 帧，至少 {need} 帧")
        for i in order:
            errors += check_image(frames[i], face=False)
        pet_actions[name] = {
            "frames": [frames[i].name for i in order],
            "fps": fps,
            "loop": loop,
        }
    pet_faces: dict[str, dict] = {}
    for name, path in faces.items():
        errors += check_image(path, face=True)
        pet_faces[name] = {"frames": [path.name]}
    pet = {"version": 1, "fps": 8, "actions": pet_actions, "expressions": pet_faces}
    return errors, warnings, pet


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("folder", type=Path)
    ap.add_argument("--member", required=True, help="文件名里的成员前缀，如 whale")
    ap.add_argument("--write", action="store_true", help="无错误时写 <素材目录>/pet.json")
    ap.add_argument("--strict", action="store_true", help="警告也算错误")
    a = ap.parse_args(argv)
    if not a.folder.is_dir():
        print(f"目录不存在：{a.folder}")
        return 1
    errors, warnings, pet = check(a.folder, a.member)
    for w in warnings:
        print("警告", w)
    for e in errors:
        print("错误", e)
    bad = bool(errors) or (a.strict and bool(warnings))
    n = sum(len(v["frames"]) for v in pet["actions"].values())
    print(
        f"{a.member}：{len(pet['actions'])} 个动作 {n} 帧，{len(pet['expressions'])} 个表情；"
        f"{len(errors)} 个错误，{len(warnings)} 个警告"
    )
    if a.write and not bad:
        target = a.folder / "pet.json"
        target.write_text(json.dumps(pet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已写入 {target}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
