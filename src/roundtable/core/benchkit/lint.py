"""工作台生成器：读窗口代码就能看出来的问题（不开浏览器），和浏览器质检的结果合在一起。

第一次真跑（2026-10-29 旅行台）发现：8 个窗口里 4 个完全手写、不用零件库，
样式各一套，一致性和商业感上不去；有的窗口写死颜色、几千行。这些在代码里一眼就能看出来，直接记成质检问题，按窗口交回作者。
"""

from __future__ import annotations

import re
from pathlib import Path

from roundtable.core.benchkit.quality import Finding

USES_KIT = re.compile(r"ctx\.kit\b|\bkit\.\w+\(|from\s+['\"]/kit/kit\.js['\"]")
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(\s*\d")
MAX_COLORS = 3  # 写死的色值超过几个算问题（颜色要用 var(--k-*)）
MAX_KB = 60  # 一个窗口的代码超过多少 KB 算太大（该拆成零件或精简）


def lint_windows(folder: Path, window_ids: list[str]) -> list[Finding]:
    out: list[Finding] = []
    no_kit, colors, big = [], [], []
    for wid in window_ids:
        path = folder / "windows" / f"{wid}.js"
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not USES_KIT.search(text):
            no_kit.append(wid)
        n = len(COLOR.findall(text))
        if n > MAX_COLORS:
            colors.append((wid, n))
        kb = len(text.encode("utf-8")) / 1024
        if kb > MAX_KB:
            big.append((wid, kb))
    where = "读代码"
    if no_kit:
        out.append(
            Finding(
                "窗口没用零件库",
                "block",
                where,
                "、".join(no_kit)
                + "：没有用 ctx.kit 的零件（表格、图表、列表、分段、空状态、提示……），"
                "整页样式会不统一；改用零件库，零件库没有的再做成 parts/ 里的通用零件",
                no_kit,
            )
        )
    if colors:
        out.append(
            Finding(
                "写死颜色",
                "warn",
                where,
                "、".join(f"{w} {n} 处" for w, n in colors)
                + "：颜色要用 var(--k-*)，深浅色和换主题才会跟着变",
                [w for w, _ in colors],
            )
        )
    if big:
        out.append(
            Finding(
                "窗口代码太大",
                "warn",
                where,
                "、".join(f"{w} {kb:.0f}KB" for w, kb in big)
                + f"：超过 {MAX_KB}KB，通用的部分拆成 parts/ 里的零件，重复的内容收进数据",
                [w for w, _ in big],
            )
        )
    return out
