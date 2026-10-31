"""工作台生成器：读窗口代码就能看出来的问题（不开浏览器），和浏览器质检的结果合在一起。

第一次真跑（2026-10-29 旅行台）发现：8 个窗口里 4 个完全手写、不用零件库，
样式各一套，一致性和商业感上不去；有的窗口写死颜色、几千行。这些在代码里一眼就能看出来，直接记成质检问题，按窗口交回作者。
"""

from __future__ import annotations

import re
from pathlib import Path

from roundtable.core.benchkit.quality import Finding

USES_KIT = re.compile(
    r"ctx\.kit\b|\bkit\.\w+\(|from\s+['\"]/kit/kit\.js['\"]"
    # 解构：const { kit } = ctx、const { seg } = kit、mount(el, { kit, store })
    r"|\{[^{}]*\bkit\b[^{}]*\}\s*=\s*ctx\b|\}\s*=\s*kit\b|\(\s*\w+\s*,\s*\{[^{}]*\bkit\b"
)
POLL = re.compile(r"\bsetInterval\s*\(")
# 自己用 svg 拼图形（参考资料里 UP 主的原话：不要让模型自己去画内容）：地图、图表、照片都有现成的
DRAW = re.compile(r"<(?:path|circle|ellipse|polygon|polyline|rect|line)\b|createElementNS\s*\(")
MAX_DRAW = 12  # 窗口代码里画图形的地方超过几处算自己画插图
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(\s*\d")
MAX_COLORS = 3  # 写死的色值超过几个算问题（颜色要用 var(--k-*)）
MAX_KB = 60  # 一个窗口的代码超过多少 KB 算太大（该拆成零件或精简）


def lint_windows(folder: Path, window_ids: list[str]) -> list[Finding]:
    out: list[Finding] = []
    no_kit, colors, big, poll, drawn = [], [], [], [], []
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
        shapes = len(DRAW.findall(text))
        if shapes > MAX_DRAW:
            drawn.append((wid, shapes))
        if POLL.search(text):
            poll.append(wid)
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
    if drawn:
        out.append(
            Finding(
                "自己画插图",
                "block",
                where,
                "、".join(f"{w} {n} 处" for w, n in drawn)
                + "：用 svg 图形拼插图、地图、图表，看起来简陋；地图用 mapFrame（真实底图），"
                "图表用零件库，照片用联网数据，都没有就用图标和真实信息",
                [w for w, _ in drawn],
            )
        )
    if poll:
        out.append(
            Finding(
                "轮询定时器",
                "warn",
                where,
                "、".join(poll)
                + "：用 setInterval 反复读数据、重画，费电又闪；别的窗口改了数据用 ctx.state.on 或"
                " ctx.bus 收通知（倒计时这类真要每秒走的除外）",
                poll,
            )
        )
    return out


# --- 查数据（2026-10-31，项目室方案 C4）---
# 第 2 次真跑里六家酒店地址全空、价格没来源：评审前就该拦下

ADDRESS_KEY = re.compile(r"(?:^|_)(?:address|addr)$|地址$", re.I)
PLACEHOLDER = re.compile(
    r"示例|样例数据|占位|待填|待补充|TODO|TBD|lorem ipsum|example\.(?:com|org)|xxx", re.I
)
FACT_KEY = re.compile(r"price|cost|fee|rate|rating|score|价格|票价|费用|汇率|评分|价钱", re.I)
SOURCE_KEY = re.compile(r"^(?:source|sources|src|ref|url|link|来源|出处)$", re.I)
DATE_KEY = re.compile(r"^(?:date|as_of|asof|updated|updated_at|checked|日期|更新|核实日期)$", re.I)
SHARED_KEYS = {  # 共用信息里的这些键，数据里同名的值要和它一致
    "currency": re.compile(r"^(?:currency|币种)$", re.I),
    "people": re.compile(r"^(?:people|travelers|travellers|guests|人数)$", re.I),
}


def _walk(node: object, path: str = ""):
    """逐个走 JSON 里的 (路径, 键, 值, 所在对象)。"""
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            yield p, str(k), v, node
            yield from _walk(v, p)
    elif isinstance(node, list):
        for i, v in enumerate(node[:500]):
            yield from _walk(v, f"{path}[{i}]")


def _has_key(obj: dict, pattern: re.Pattern[str]) -> bool:
    return any(pattern.search(str(k)) and v not in ("", None, []) for k, v in obj.items())


def lint_data(folder: Path, window_ids: list[str], shared: dict | None = None) -> list[Finding]:
    """读每个窗口的数据文件：地址为空、写「示例」占位、带价格评分却没来源和日期、
    和共用信息矛盾（币种、人数）。地址空、占位、矛盾直接打回；缺来源日期是建议修。"""
    import json

    shared = shared or {}
    want = {
        name: v
        for name, pat in SHARED_KEYS.items()
        for k, v in shared.items()
        if pat.search(str(k)) and isinstance(v, (str, int, float))
    }
    out: list[Finding] = []
    where = "查数据"
    for wid in window_ids:
        path = folder / "data" / f"{wid}.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            out.append(Finding("数据文件看不懂", "block", where, f"{wid}：不是合法的 JSON", [wid]))
            continue
        empty, holders, conflict = [], [], []
        unsourced = 0
        top_ok = isinstance(data, dict) and _has_key(data, SOURCE_KEY) and _has_key(data, DATE_KEY)
        for p, k, v, obj in _walk(data):
            if ADDRESS_KEY.search(k) and (v is None or (isinstance(v, str) and not v.strip())):
                empty.append(p)
            if isinstance(v, str) and PLACEHOLDER.search(v):
                holders.append(f"{p}「{v[:20]}」")
            for name, pat in SHARED_KEYS.items():
                if (
                    name in want
                    and pat.search(k)
                    and isinstance(v, (str, int, float))
                    and str(v).strip().lower() != str(want[name]).strip().lower()
                ):
                    conflict.append(f"{p}={v}（共用信息是 {want[name]}）")
            if (
                FACT_KEY.search(k)
                and isinstance(v, (int, float, str))
                and v not in ("", None)
                and not top_ok
                and not (_has_key(obj, SOURCE_KEY) and _has_key(obj, DATE_KEY))
            ):
                unsourced += 1
        if empty:
            out.append(
                Finding(
                    "地址为空",
                    "block",
                    where,
                    f"{wid}：{len(empty)} 处地址是空的（{'、'.join(empty[:3])}）；"
                    "填真实地址，查不到就去掉这一条，别留空",
                    [wid],
                )
            )
        if holders:
            out.append(
                Finding(
                    "占位数据",
                    "block",
                    where,
                    f"{wid}：{'、'.join(holders[:3])} 是占位文字；换成真实内容，"
                    "要按用户条件生成的用 ctx.askJson 现场生成",
                    [wid],
                )
            )
        if conflict:
            out.append(
                Finding(
                    "和共用信息矛盾",
                    "block",
                    where,
                    f"{wid}：{'、'.join(conflict[:3])}；统一从 ctx.shared 读，不另写一份",
                    [wid],
                )
            )
        if unsourced:
            out.append(
                Finding(
                    "数据没标来源和日期",
                    "warn",
                    where,
                    f"{wid}：{unsourced} 处价格、评分这类数字没写来源（source）和日期（date）；"
                    "写在这一条里，或在文件最外层统一写",
                    [wid],
                )
            )
    return out
