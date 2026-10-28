"""导出的样式选项：字号、配色模板、PPT 比例、是否附题目 / 过程。全部是本地排版参数，与模型无关。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    label: str
    accent: str  # 标题、色带的颜色（RRGGBB）
    font: str  # 中文字体
    latin: str  # 西文字体
    serif: bool = False  # 网页版用衬线字体


THEMES: dict[str, Theme] = {
    "blue": Theme("简洁蓝", "2F5597", "Microsoft YaHei", "Microsoft YaHei"),
    "black": Theme("商务黑", "1F2328", "Microsoft YaHei", "Arial"),
    "green": Theme("清新绿", "2E7D32", "Microsoft YaHei", "Microsoft YaHei"),
    "serif": Theme("学术宋体", "7B2D26", "SimSun", "Times New Roman", serif=True),
}

SIZES: dict[str, tuple[str, float]] = {
    "small": ("小", 0.9),
    "medium": ("中", 1.0),
    "large": ("大", 1.15),
}

RATIOS: dict[str, tuple[int, int]] = {  # PPT 页面尺寸（EMU）
    "16:9": (12192000, 6858000),
    "4:3": (9144000, 6858000),
}


@dataclass(frozen=True)
class Style:
    size: str = "medium"
    theme: str = "blue"
    ratio: str = "16:9"
    question: bool = True  # 文档开头附上题目
    process: bool = False  # 文末附上过程（各人的初稿与修订）

    def __post_init__(self) -> None:
        for value, allowed, what in (
            (self.size, SIZES, "字号"),
            (self.theme, THEMES, "配色"),
            (self.ratio, RATIOS, "PPT 比例"),
        ):
            if value not in allowed:
                raise ValueError(f"不支持的{what}：{value}（可选 {'、'.join(allowed)}）")

    @property
    def scale(self) -> float:
        return SIZES[self.size][1]

    @property
    def look(self) -> Theme:
        return THEMES[self.theme]


def options() -> dict[str, list[dict[str, str]]]:
    """给界面列出可选项（值和中文名）。"""
    return {
        "themes": [{"value": k, "label": t.label} for k, t in THEMES.items()],
        "sizes": [{"value": k, "label": v[0]} for k, v in SIZES.items()],
        "ratios": [{"value": k, "label": k} for k in RATIOS],
    }
