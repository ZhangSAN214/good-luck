"""检查小人差分素材并生成 pet.json。

素材在用户本机（data/ 不进 git）。目录约定见 docs/agent/差分清单.md：

    <素材目录>/<成员 id>/<动作>/00.png 01.png …           动作帧
    <素材目录>/<成员 id>/face/<表情>/00.png …              表情帧
    <素材目录>/<成员 id>/pet.json                          本脚本生成

用法（在项目根目录）：
    python scripts/check_pet_assets.py                 # 检查全部成员，只报告
    python scripts/check_pet_assets.py --member xxx    # 只检查某位成员（可重复）
    python scripts/check_pet_assets.py --write         # 检查通过后写出 pet.json
    python scripts/check_pet_assets.py --dir D:/素材 --size 512

退出码：有「错误」为 1；只有「提醒」为 0。只检查，不改动图片；--write 只写 pet.json。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from roundtable.core.members_art import PET_ACTIONS, PET_EXPRESSIONS, PET_MAX_FRAMES

DEFAULT_DIR = "data/avatars/roundtable_assets"
DEFAULT_SIZE = 512
FACE_DIR = "face"
FRAME_NAME = re.compile(r"^(\d{2,3})\.(png|webp)$")
MAX_FRAME_BYTES = 300 * 1024
ALPHA_SOLID = 16  # alpha 高于它算「有内容」
CORNER = 4  # 四角各取这么大的方块检查透明
FLOAT_ACTIONS = {"jump", "celebrate", "drag"}  # 离地的动作不检查脚底锚点
# 动作：(最少帧, 推荐帧, 帧率, 是否循环)。blink 是后端动作名，缺失不报错。
ACTION_SPEC: dict[str, tuple[int, int, int, bool]] = {
    "idle": (2, 4, 4, True),
    "blink": (2, 3, 12, False),
    "walk": (4, 6, 8, True),
    "jump": (3, 5, 10, False),
    "wave": (3, 6, 8, False),
    "think": (2, 4, 4, True),
    "talk": (2, 4, 8, True),
    "type": (2, 4, 8, True),
    "celebrate": (4, 6, 10, False),
    "sleepy": (2, 3, 3, True),
    "sleep": (2, 4, 2, True),
    "drag": (2, 3, 8, True),
    "land": (2, 3, 12, False),
    # 成员专属动作（克劳德用；数值取自已验收素材的 pet.json）
    "talk_book": (2, 3, 6, False),
    "letter": (2, 6, 3, True),
    "gift": (2, 6, 2, False),
    "curtsy": (2, 4, 3, False),
    "flip": (2, 4, 3, True),
    "bookface": (2, 4, 3, True),
}
FACE_FPS = 4
REQUIRED_ACTIONS = ("idle",)


@dataclass
class Report:
    member: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    actions: dict[str, list[str]] = field(default_factory=dict)
    expressions: dict[str, list[str]] = field(default_factory=dict)
    pet: dict | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def _frames_in(folder: Path, label: str, report: Report) -> list[str]:
    """文件夹里按序号排好的帧文件名；命名不对 / 序号不连续记错误。"""
    names: list[tuple[int, str]] = []
    for path in sorted(folder.iterdir()):
        if path.name.startswith(".") or path.is_dir():
            continue
        match = FRAME_NAME.match(path.name)
        if not match:
            report.errors.append(
                f"{label}/{path.name}：文件名应为 00.png、01.png …（两三位数字，png 或 webp）"
            )
            continue
        names.append((int(match.group(1)), path.name))
    names.sort()
    for expected, (number, name) in enumerate(names):
        if number != expected:
            report.errors.append(f"{label}：序号不连续，缺少 {expected:02d}（现有 {name}）")
            break
    if len(names) > PET_MAX_FRAMES:
        report.errors.append(f"{label}：{len(names)} 帧，最多 {PET_MAX_FRAMES} 帧")
    return [name for _, name in names]


def _check_image(path: Path, label: str, size: int, action: str, report: Report) -> None:
    from PIL import Image

    try:
        with Image.open(path) as im:
            im.load()
            image = im.convert("RGBA")
            has_alpha = "A" in im.getbands() or "transparency" in im.info
    except Exception as exc:  # noqa: BLE001 - 坏图片只记录，不中断检查
        report.errors.append(f"{label}：打不开图片（{type(exc).__name__}）")
        return
    if path.stat().st_size > MAX_FRAME_BYTES:
        kb = path.stat().st_size // 1024
        report.warnings.append(f"{label}：文件 {kb}KB，偏大（建议 ≤ {MAX_FRAME_BYTES // 1024}KB）")
    if image.size != (size, size):
        report.errors.append(f"{label}：尺寸 {image.width}×{image.height}，应为 {size}×{size}")
        return
    if not has_alpha:
        report.errors.append(f"{label}：没有透明通道（背景必须透明）")
        return
    alpha = image.getchannel("A")
    solid = alpha.point(lambda v: 255 if v > ALPHA_SOLID else 0)
    for name, box in (
        ("左上", (0, 0, CORNER, CORNER)),
        ("右上", (size - CORNER, 0, size, CORNER)),
        ("左下", (0, size - CORNER, CORNER, size)),
        ("右下", (size - CORNER, size - CORNER, size, size)),
    ):
        if solid.crop(box).getbbox():
            report.errors.append(f"{label}：{name}角不透明（像是带了背景）")
            return
    bbox = solid.getbbox()
    if bbox is None:
        report.errors.append(f"{label}：整张图都是透明的")
        return
    left, top, right, bottom = bbox
    if left == 0 or top == 0 or right == size or bottom == size:
        report.warnings.append(f"{label}：内容贴到画布边缘，可能被裁切")
    if action in FLOAT_ACTIONS or action.startswith(f"{FACE_DIR}/"):
        return
    if bottom < size * 0.88:
        report.warnings.append(
            f"{label}：脚底离画布底边 {size - bottom}px，锚点应在底边附近（离地动作除外）"
        )
    center = (left + right) / 2
    if abs(center - size / 2) > size * 0.15:
        off = abs(center - size / 2)
        report.warnings.append(f"{label}：内容水平偏离中线 {off:.0f}px，锚点应在底边正中")


def check_member(root: Path, member_id: str, size: int = DEFAULT_SIZE) -> Report:
    report = Report(member_id)
    base = root / member_id
    if not base.is_dir():
        report.errors.append(f"没有这位成员的素材文件夹：{member_id}/")
        return report
    known = set(PET_ACTIONS)
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        name = child.name
        if name == FACE_DIR:
            for sub in sorted(p for p in child.iterdir() if p.is_dir()):
                if sub.name not in PET_EXPRESSIONS:
                    report.warnings.append(f"{FACE_DIR}/{sub.name}：不是已知表情，已忽略")
                    continue
                label = f"{FACE_DIR}/{sub.name}"
                files = _frames_in(sub, label, report)
                if files:
                    report.expressions[sub.name] = [f"{label}/{f}" for f in files]
        elif name in known:
            files = _frames_in(child, name, report)
            if files:
                report.actions[name] = [f"{name}/{f}" for f in files]
        else:
            report.warnings.append(f"{name}/：不是已知动作，已忽略")
    for action in REQUIRED_ACTIONS:
        if action not in report.actions:
            report.errors.append(f"缺少必需的动作：{action}")
    for action in PET_ACTIONS:
        if action not in report.actions and action not in REQUIRED_ACTIONS:
            report.warnings.append(f"缺少动作 {action}（会回退为程序动画）")
        elif action in report.actions:
            minimum, recommended, _, _ = ACTION_SPEC[action]
            n = len(report.actions[action])
            if n < minimum:
                report.warnings.append(
                    f"{action}：只有 {n} 帧，至少 {minimum} 帧（推荐 {recommended}）"
                )
    for expr in PET_EXPRESSIONS:
        if expr not in report.expressions:
            report.warnings.append(f"缺少表情 {expr}（会回退为头顶符号）")
    for action, files in report.actions.items():
        for rel in files:
            _check_image(base / rel, rel, size, action, report)
    for expr, files in report.expressions.items():
        for rel in files:
            _check_image(base / rel, rel, size, f"{FACE_DIR}/{expr}", report)
    report.pet = build_pet(report)
    return report


def build_pet(report: Report) -> dict | None:
    """按检查到的帧生成 pet.json 内容（帧路径相对 pet.json 所在的成员文件夹）。"""
    if not report.actions and not report.expressions:
        return None
    actions = {}
    for name, frames in report.actions.items():
        _, _, fps, loop = ACTION_SPEC[name]
        actions[name] = {"frames": frames, "fps": fps, "loop": loop}
    expressions = {
        name: {"frames": frames, "fps": FACE_FPS, "loop": False}
        for name, frames in report.expressions.items()
    }
    return {"version": 1, "fps": 8, "actions": actions, "expressions": expressions}


def member_ids(root: Path, only: list[str]) -> list[str]:
    if only:
        return only
    found: list[str] = []
    index = root / "members.json"
    if index.is_file():
        try:
            data = json.loads(index.read_text(encoding="utf-8"))
            rows = data.get("members", [])
            found = [m["id"] for m in rows if isinstance(m, dict) and m.get("id")]
        except (OSError, ValueError, KeyError, TypeError):
            found = []
    found = [m for m in found if (root / m).is_dir()]
    if found:
        return found
    return sorted(p.name for p in root.iterdir() if p.is_dir() and (p / "idle").is_dir())


def render(report: Report) -> str:
    lines = [f"== {report.member} =="]
    lines.append(
        f"动作 {len(report.actions)}/{len(PET_ACTIONS)}，"
        f"表情 {len(report.expressions)}/{len(PET_EXPRESSIONS)}"
    )
    lines += [f"  错误：{e}" for e in report.errors]
    lines += [f"  提醒：{w}" for w in report.warnings]
    lines.append("  结果：通过" if report.ok else f"  结果：不通过（{len(report.errors)} 个错误）")
    return "\n".join(lines)


def write_pet(root: Path, report: Report) -> Path | None:
    if not report.ok or report.pet is None:
        return None
    path = root / report.member / "pet.json"
    path.write_text(json.dumps(report.pet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检查小人差分素材并生成 pet.json")
    parser.add_argument("--dir", help=f"素材目录（默认取 ROUNDTABLE_ASSETS_DIR 或 {DEFAULT_DIR}）")
    parser.add_argument("--member", action="append", default=[], help="只检查这位成员 id（可重复）")
    parser.add_argument("--size", type=int, default=DEFAULT_SIZE, help="画布边长，默认 512")
    parser.add_argument("--write", action="store_true", help="检查通过的成员写出 pet.json")
    args = parser.parse_args(argv)
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("需要 Pillow：pip install pillow", file=sys.stderr)
        return 2
    root = Path(args.dir or os.environ.get("ROUNDTABLE_ASSETS_DIR") or DEFAULT_DIR)
    if not root.is_dir():
        print(f"找不到素材目录：{root}", file=sys.stderr)
        return 2
    ids = member_ids(root, args.member)
    if not ids:
        print(f"{root} 里没有找到成员素材（需要 <成员 id>/idle/00.png 等）", file=sys.stderr)
        return 2
    failed = 0
    for member_id in ids:
        report = check_member(root, member_id, args.size)
        print(render(report))
        if not report.ok:
            failed += 1
        elif args.write:
            path = write_pet(root, report)
            print(f"  已写出 {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
