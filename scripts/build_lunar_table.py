"""生成日历插件用的农历与二十四节气表（src/roundtable/core/almanac/lunar_table.json）。

圆桌运行时不需要农历库：农历月份长度、闰月、节气日期都预先算好放进这张表（2000～2100 年，
北京时间），离线、免费。本脚本只在要扩大年份范围时由开发者运行一次，需要临时装开源库
lunar_python（MIT）：

    pip install lunar_python
    python scripts/build_lunar_table.py
"""

from __future__ import annotations

import json
from pathlib import Path

from lunar_python import Lunar, LunarYear, Solar

FIRST, LAST = 2000, 2100
OUT = Path(__file__).resolve().parents[1] / "src/roundtable/core/almanac/lunar_table.json"


def lunar_year(y: int) -> dict:
    """农历 y 年：正月初一的公历日期、按顺序的各月 [月, 是否闰月, 天数]。"""
    start = Lunar.fromYmd(y, 1, 1).getSolar()
    months = []
    for m in LunarYear.fromYear(y).getMonths():
        if m.getYear() != y:
            continue
        months.append([abs(m.getMonth()), int(m.isLeap()), m.getDayCount()])
    return {"start": start.toYmd(), "months": months}


def solar_terms(y: int) -> dict[str, str]:
    """公历 y 年里的二十四节气：名字 → MM-DD。"""
    out: dict[str, str] = {}
    for lunar_y in (y - 1, y, y + 1):
        table = Lunar.fromYmd(lunar_y, 6, 1).getJieQiTable()
        for name, solar in table.items():
            if not all("一" <= ch <= "鿿" for ch in name):
                continue  # 表里还有拼音键（下一年的同名节气），跳过
            if solar.getYear() == y:
                out[name] = f"{solar.getMonth():02d}-{solar.getDay():02d}"
    return dict(sorted(out.items(), key=lambda kv: kv[1]))


def main() -> None:
    table = {
        "source": "lunar_python (MIT)，北京时间",
        "first": FIRST,
        "last": LAST,
        "years": {str(y): lunar_year(y) for y in range(FIRST, LAST + 1)},
        "terms": {str(y): solar_terms(y) for y in range(FIRST, LAST + 1)},
    }
    assert all(len(t) == 24 for t in table["terms"].values()), "每年应有 24 个节气"
    OUT.write_text(json.dumps(table, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"已写入 {OUT}（{OUT.stat().st_size // 1024} KB）")
    assert Solar.fromYmd(2026, 2, 17).getLunar().getMonth() == 1


if __name__ == "__main__":
    main()
