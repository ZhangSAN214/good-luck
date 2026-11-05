"""每个台有自己的样子（docs/KIT.md「生成器第二版 · 第 4 块」）。

- **不撞样子**：拆需求的助手给的配色、主色、字体气质、材质、背景，和最近做过的几个台比；
  撞得太像（同一套配色 + 字体 + 材质，或主色几乎一样）就换掉撞上的那几项，换成最近没用过的，
  记下换了什么、为什么。只改推荐，签字时你在选型台上照样能改回去。
- **评分标尺**：你挑几张截图放进标尺文件夹，文件名写分数（如「8分-清爽的仪表盘.png」）；
  评审时把它们拷进这个台的 ``ruler/``，告诉评审助手「这是 8 分的样子」，照着这把尺子打分。
  截图只放在你电脑上（数据目录里），不进仓库。

这里只做纯数据和文件处理，不调模型。
"""

from __future__ import annotations

import colorsys
import json
import random
import re
import shutil
from pathlib import Path
from typing import Any

# 一眼能分出两个台的几项（顺序 = 撞了以后先换哪个）
LOOK_KEYS = ("theme", "accent", "type", "material", "bg")
ALIKE = 3  # 这几项里有几项一样就算撞了
HUE_NEAR = 30  # 两个主色的色相差在这个度数以内算一样
RULER_EXT = (".png", ".jpg", ".jpeg", ".webp")
RULER_NAME = re.compile(r"^(\d{1,2})\s*分[\s_-]*(.*)$")
RULER_MAX = 6


def _hue(hex_color: str) -> float | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hex_color or ""))
    if not m:
        return None
    v = m.group(1)
    r, g, b = (int(v[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, _, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360 if s > 0.15 else None  # 灰色没有色相，不算撞


def _near(a: str, b: str) -> bool:
    ha, hb = _hue(a), _hue(b)
    if ha is None or hb is None:
        return False
    d = abs(ha - hb) % 360
    return min(d, 360 - d) <= HUE_NEAR


def signature(design: dict[str, Any], theme: str) -> dict[str, str]:
    picks = (design or {}).get("picks") or {}
    return {
        "theme": theme,
        "accent": str(picks.get("accent") or ""),
        "type": str(picks.get("type") or "sans"),
        "material": str(picks.get("material") or "solid"),
        "bg": str(picks.get("bg") or "soft"),
    }


def _sig(other: dict[str, Any]) -> dict[str, str]:
    return signature(other.get("design") or {}, str(other.get("theme") or ""))


def same_keys(a: dict[str, str], b: dict[str, str]) -> list[str]:
    out = []
    for k in LOOK_KEYS:
        if k == "accent":
            if a["accent"] and b["accent"] and _near(a["accent"], b["accent"]):
                out.append(k)
        elif a.get(k) == b.get(k):
            out.append(k)
    return out


def _rotate(hex_color: str, degrees: float) -> str:
    v = hex_color.lstrip("#")
    r, g, b = (int(v[i : i + 2], 16) / 255 for i in (0, 2, 4))
    h, li, s = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((h + degrees / 360) % 1, li, s)
    return "#" + "".join(f"{round(x * 255):02x}" for x in (r, g, b))


def distinct(
    design: dict[str, Any],
    theme: str,
    recent: list[dict[str, Any]],
    options: dict[str, list[str]],
    rng: random.Random,
) -> tuple[str, list[dict[str, str]]]:
    """和最近的台撞了样子就换：返回新的配色名和换了哪几项（design 原地改）。

    recent：最近几个台的 ``{"title", "theme", "design"}``；options：每项可选的值
    （theme 来自零件库主题，其余来自 DESIGN_DIMS）。"""
    picks = design.setdefault("picks", {})
    changes: list[dict[str, str]] = []
    mine = signature(design, theme)
    for other in recent:
        theirs = _sig(other)
        hit = same_keys(mine, theirs)
        if len(hit) < ALIKE:
            continue
        # 撞了：从最前面的一项起换，换到只剩 ALIKE - 1 项一样为止
        for k in hit[: len(hit) - ALIKE + 1]:
            if k == "accent":
                new = _rotate(mine["accent"], rng.choice((120, 150, 210, 240)))
            else:
                used = {_sig(o)[k] for o in recent}
                pool = [v for v in options.get(k, []) if v not in used] or [
                    v for v in options.get(k, []) if v != mine[k]
                ]
                if not pool:
                    continue
                new = rng.choice(sorted(pool))
            why = f"和「{other.get('title') or '上一个台'}」太像"
            changes.append({"key": k, "old": mine[k], "new": new, "why": why})
            mine[k] = new
    if not changes:
        return theme, []
    for c in changes:
        if c["key"] != "theme":
            picks[c["key"]] = c["new"]
            design.setdefault("recommend", {})[c["key"]] = c["new"]
    new_theme = mine["theme"]
    if new_theme != theme:
        picks["theme"] = new_theme
        design.setdefault("recommend", {})["theme"] = new_theme
    return new_theme, changes


def recent_looks(root: Path | None, skip: str, limit: int = 5) -> list[dict[str, Any]]:
    """最近做过的几个台的样子（读各自的 spec.json）。"""
    if root is None or not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("*/spec.json"), reverse=True):
        if p.parent.name == skip:
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"title": d.get("title"), "theme": d.get("theme"), "design": d.get("design")})
        if len(out) >= limit:
            break
    return out


# --- 评分标尺 --------------------------------------------------------------------------


def ruler_refs(folder: Path | None) -> list[dict[str, Any]]:
    """标尺文件夹里的截图：文件名开头写分数（「8分-清爽的仪表盘.png」），按分数从高到低。"""
    if folder is None or not folder.is_dir():
        return []
    out = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in RULER_EXT or not p.is_file():
            continue
        m = RULER_NAME.match(p.stem)
        if not m or not 0 <= int(m.group(1)) <= 10:
            continue
        out.append({"file": p.name, "score": int(m.group(1)), "note": m.group(2).strip()[:60]})
    out.sort(key=lambda x: (-x["score"], x["file"]))
    return out[:RULER_MAX]


def ruler_name(score: int, note: str, ext: str) -> str:
    """存进标尺文件夹时的文件名：分数开头，说明里只留字母、数字、汉字。"""
    clean = re.sub(r"[^\w一-鿿]+", "-", note).strip("-_")[:30]
    return f"{score}分" + (f"-{clean}" if clean else "") + ext


def copy_ruler(src: Path | None, dest: Path) -> list[dict[str, Any]]:
    """评审前把标尺截图拷进这个台的 ruler/（评审助手只能看工作文件夹里的东西）。"""
    refs = ruler_refs(src)
    if not refs:
        return []
    assert src is not None
    dest.mkdir(parents=True, exist_ok=True)
    for r in refs:
        shutil.copyfile(src / r["file"], dest / r["file"])
    return refs


def ruler_text(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "（没有标尺截图，按评分项自己的标准打）"
    return "\n".join(
        f"- ruler/{r['file']}：这是 {r['score']} 分的样子"
        + (f"（{r['note']}）" if r["note"] else "")
        for r in refs
    )
