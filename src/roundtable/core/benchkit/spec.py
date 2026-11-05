"""工作台规格：拆需求的助手交回来的 JSON → 校验、修正成生成器能放心用的样子；再按规格画出外壳页面。

外壳（顶栏 + 网格 + 每个窗口的卡片）由程序按规格生成，品质稳定；助手只写每个窗口里面的内容
（``windows/<id>.js``，默认导出 ``mount(el, ctx)``）和示例数据（``data/<id>.json``）。
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from roundtable.core.benchkit.intent import MAX_STAGES, stages_of
from roundtable.core.benchkit.quality import KIT_DIR
from roundtable.core.textutil import flat_cut as _text

_ID = re.compile(r"[^a-z0-9-]+")
SHARED_MAX_CHARS = 6000
STATE_MAX = 10


def manifest() -> dict[str, Any]:
    return json.loads((KIT_DIR / "manifest.json").read_text(encoding="utf-8"))


def icon_names() -> set[str]:
    text = (KIT_DIR / "icons.js").read_text(encoding="utf-8")
    return set(re.findall(r'^"([\w-]+)":', text, re.M))


def parts_brief(m: dict[str, Any] | None = None) -> str:
    """零件清单的简写（给拆需求的助手看）：名字、用途；最后附图标名。"""
    m = m or manifest()
    lines = [f"- {p['name']}：{p['use']}" for p in m["parts"]]
    lines.append("图标：" + "、".join(sorted(icon_names())))
    return "\n".join(lines)


@dataclass
class Window:
    id: str
    title: str
    sub: str = ""
    icon: str = "layout-grid"
    span: int = 6
    purpose: str = ""
    parts: list[str] = field(default_factory=list)
    data: str = ""
    actions: list[str] = field(default_factory=list)
    # 在版型里的位置（D 布局模板）：「侧栏列表（电脑上在主视觉右边）」这类大白话，写窗口时照着排
    zone: str = ""
    # 资源检索员按这份清单去找（第 3 块）：kind、what、query（英文搜索词）、count；
    # 数据写 service（bench.net 里的名字）或 why；资料写 urls
    wants: list[dict[str, Any]] = field(default_factory=list)

    @property
    def file(self) -> str:
        return f"windows/{self.id}.js"

    @property
    def data_file(self) -> str:
        return f"data/{self.id}.json"


@dataclass
class Spec:
    title: str
    subtitle: str = ""
    icon: str = "layers"
    theme: str = "ink"
    windows: list[Window] = field(default_factory=list)
    needs: list[dict[str, str]] = field(default_factory=list)
    # 各窗口共用的基本信息（日期、人数、币种、场景……）：只放一份，窗口都从 ctx.shared 读
    shared: dict[str, Any] = field(default_factory=dict)
    # 窗口之间的消息约定：名字、谁发、谁收、detail 里有什么（ctx.bus 上照这个发和收）
    events: list[dict[str, Any]] = field(default_factory=list)
    # 几个窗口一起读写的用户数据（行程里加了什么、记了哪些账……）：键 → 结构和谁来改。
    # 都放在 ctx.state 里，谁改了大家都看得到，不各存一份（「已加入」两边对不上就是各存一份）
    state: dict[str, str] = field(default_factory=dict)
    # 验收清单：用户这句话拆出来的要求（r1、r2……）和底线标准（b1、b2……），每条写怎么检查、
    # 归哪几个窗口负责。评审逐条判做到没有，有一条没做到就不过线
    requirements: list[dict[str, Any]] = field(default_factory=list)
    # 主视觉窗口（地图、3D、图纸、照片这类真素材）：排在最前面、占大半屏
    hero: str = ""
    # 设计选型（版型、配色、材质、密度、圆角、字体气质、参考作品）：拆需求的助手给推荐和理由，
    # 签字时按你在选型台上点的定下来。外壳按它排窗口、加样式（<html data-k-*>）
    design: dict[str, Any] = field(default_factory=dict)
    # 分段计划（竖着切，每段都能走通）：n、title、goal、try、windows、accept、skip、risk
    # （intent.stages_of）
    stages: list[dict[str, Any]] = field(default_factory=list)
    # 建议池：用户没要、以后可以加的功能（追溯不到原话的不做，放这里）
    pool: list[str] = field(default_factory=list)
    # 工作台类型（编辑器、游戏模拟、看板……，见 bench.skills.kinds）：写窗口时按那一类的做法来
    kind: str = ""
    # 产出形式（K1）：page = 一页卡片窗口拼盘；app = 在底座（BASES）上做的多文件应用，
    # 窗口变成底座上的功能（面板 + 按钮 + 工具）。由程序按 kind 和配置 bench.apps.bases 定
    output: str = "page"
    base: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def window(self, wid: str) -> Window | None:
        return next((w for w in self.windows if w.id == wid), None)


class SpecError(ValueError):
    pass


ASSET_KINDS = ("photo", "illustration", "texture", "model", "icon", "font", "data", "doc")
WANTS_PER_WINDOW = 4


def wants_of(raw: Any) -> list[dict[str, Any]]:
    """拆需求给每个窗口列的素材和数据清单：认得的种类才留，每个窗口最多 4 项。"""
    out: list[dict[str, Any]] = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict) or a.get("kind") not in ASSET_KINDS:
            continue
        item: dict[str, Any] = {"kind": a["kind"], "what": _text(a.get("what"), 60)}
        if a["kind"] == "data":
            item["service"] = re.sub(r"[^a-z0-9_-]", "", str(a.get("service") or "").lower())[:30]
            item["why"] = _text(a.get("why"), 80)
        elif a["kind"] == "doc":
            item["urls"] = [str(u).strip()[:300] for u in (a.get("urls") or [])][:3]
        else:
            item["query"] = re.sub(r"[^\w\s-]", " ", _text(a.get("query"), 60)).strip()
            try:
                item["count"] = max(1, min(4, int(a.get("count") or 1)))
            except (TypeError, ValueError):
                item["count"] = 1
            if not item["query"]:
                continue
        if item["what"] or item.get("urls") or item.get("service"):
            out.append(item)
        if len(out) >= WANTS_PER_WINDOW:
            break
    return out


PROBE_DO = ("fill", "select", "click")


def probe_of(raw: Any) -> dict[str, Any] | None:
    """验收要求的自动检查：最多 4 步（填写 / 选择 / 点击），做完看哪些字出现、页面变没变。
    看不懂或没有能看的结果就当没写。"""
    if not isinstance(raw, dict):
        return None
    steps = []
    for x in raw.get("steps") or []:
        if not isinstance(x, dict) or x.get("do") not in PROBE_DO:
            continue
        target = _text(x.get("target"), 40)
        if not target:
            continue
        step = {"do": x["do"], "target": target}
        if x["do"] != "click":
            value = _text(x.get("value"), 80)
            if not value:
                continue
            step["value"] = value
        steps.append(step)
    steps = steps[:4]
    expect = [t for t in (_text(e, 40) for e in raw.get("expect") or []) if t][:4]
    changes = raw.get("changes") is True
    if not steps or not (expect or changes):
        return None
    return {"steps": steps, "expect": expect, "changes": changes}


# 设计选型的目录（程序定，拆需求的助手只能从里面挑并说理由）。每一项都直接对应外壳上的样式
# 或窗口排布，选型台的预览和成品用的是同一套样式，所见即所得。
THEME_LABELS = {
    "ink": ("墨蓝", "沉稳专业，适合工具、数据、办公"),
    "paper": ("纸本", "暖白纸感，适合阅读、手账、学习"),
    "crimson": ("赤红", "有冲劲，适合运动、游戏、比赛"),
    "forest": ("森绿", "自然健康，适合户外、植物、生活"),
    "amber": ("琥珀", "温暖，适合美食、家居、手作"),
    "ocean": ("海蓝", "清爽，适合旅行、出行、海边"),
}
# 每项：(名字, 分组, 选项)；分组决定选型台上怎么排（结构、色彩、质感、文字、控件）
DESIGN_DIMS: dict[str, tuple[str, str, list[tuple[str, str, str]]]] = {
    "layout": (
        "版型",
        "结构",
        [
            (
                "hero-side",
                "主视觉 + 侧栏列表 + 详情",
                "大画面在左占三分之二，右边上面列表、下面详情",
            ),
            ("hero-strip", "主视觉 + 底部数据条", "大画面占满一整行，下面一排数字、图表小窗"),
            ("quad", "四分屏", "四块一样大，两行两列，主视觉在左上"),
            ("flow", "流程四区", "按步骤从左到右：准备 → 主画面 → 检查，下面一整条是交付"),
            ("workspace", "编辑器三栏", "左边窄工具栏、中间画布、右边属性，像修图和设计软件"),
            ("stream", "单列阅读", "从上到下一列，像文档、报告、教程"),
        ],
    ),
    "width": (
        "页面宽度",
        "结构",
        [
            ("narrow", "窄", "内容集中在中间，适合阅读"),
            ("normal", "标准", "一般情况"),
            ("full", "全宽", "铺满屏幕，适合大屏和画布"),
        ],
    ),
    "topbar": (
        "顶栏",
        "结构",
        [
            ("normal", "标准", "左边图标和名字"),
            ("banner", "大标题横幅", "主色横幅加大号标题，像产品首页"),
            ("compact", "紧凑", "一行小字，把空间让给内容"),
        ],
    ),
    "herosize": (
        "主视觉高度",
        "结构",
        [
            ("low", "矮", "约半屏，下面的窗口更早露出来"),
            ("normal", "中", "约七成屏"),
            ("tall", "高", "接近满屏，沉浸感最强"),
        ],
    ),
    "bg": (
        "背景",
        "色彩",
        [
            ("soft", "柔光渐变", "角落一抹主色光晕"),
            ("plain", "纯色", "干净，不抢内容"),
            ("grid", "网格点", "细点阵，像设计稿和图纸"),
            ("image", "素材图", "用你拖进来、标成「背景」的图，盖一层淡色保证看得清"),
        ],
    ),
    "material": (
        "材质",
        "质感",
        [
            ("solid", "实色卡片", "白底卡片加轻阴影，最稳"),
            ("glass", "液态玻璃", "半透明磨砂，背后的颜色透出来"),
            ("paper", "纸感", "没有阴影、细边框，像纸面"),
            ("flat", "扁平", "没有边框和阴影，用底色分块"),
            ("outline", "线框", "透明底、粗一点的边框，像图纸"),
        ],
    ),
    "corner": (
        "圆角",
        "质感",
        [
            ("sharp", "方正", "小圆角，干练"),
            ("normal", "适中", "一般情况"),
            ("round", "圆润", "大圆角，亲和"),
        ],
    ),
    "shadow": (
        "阴影",
        "质感",
        [
            ("none", "无", "完全平面"),
            ("soft", "轻", "一般情况"),
            ("deep", "重", "卡片浮起来，层次更强"),
        ],
    ),
    "density": (
        "密度",
        "质感",
        [
            ("cozy", "宽松", "留白多，适合触屏和展示"),
            ("normal", "标准", "一般情况"),
            ("compact", "紧凑", "一屏放更多，适合数据多的"),
        ],
    ),
    "type": (
        "字体气质",
        "文字",
        [
            ("sans", "现代无衬线", "清楚，一般情况"),
            ("serif", "衬线编辑感", "标题用宋体 / 衬线字，像杂志"),
            ("mono", "等宽技术感", "标题用等宽字，像终端和图纸"),
        ],
    ),
    "size": (
        "字号",
        "文字",
        [
            ("small", "小", "信息多时用"),
            ("normal", "标准", "一般情况"),
            ("large", "大", "给长辈用、远看、触屏"),
        ],
    ),
    "tone": (
        "文案语气",
        "文字",
        [
            ("plain", "平实", "说清楚就好"),
            ("formal", "正式", "像专业软件和报告"),
            ("friendly", "亲切", "像朋友提醒"),
            ("lively", "活泼", "带点俏皮，适合游戏和娱乐"),
        ],
    ),
    "button": (
        "按钮形状",
        "控件",
        [
            ("rounded", "圆角", "一般情况"),
            ("pill", "胶囊", "两头全圆，柔和"),
            ("square", "方角", "利落，像专业工具"),
        ],
    ),
    "cardhead": (
        "窗口标题",
        "控件",
        [
            ("icon", "图标块", "标题前一个带底色的图标"),
            ("plain", "只有文字", "更安静"),
            ("bar", "彩色顶条", "窗口顶上一条主色"),
        ],
    ),
    "motion": (
        "动效",
        "控件",
        [
            ("none", "无", "打开就是最终样子"),
            ("soft", "轻", "窗口依次淡入"),
            ("rich", "丰富", "淡入再加悬停浮起"),
        ],
    ),
}
DESIGN_GROUPS = ("结构", "色彩", "质感", "文字", "控件")
DESIGN_DEFAULTS = {
    "layout": "hero-side",
    "width": "normal",
    "topbar": "normal",
    "herosize": "normal",
    "bg": "soft",
    "material": "solid",
    "corner": "normal",
    "shadow": "soft",
    "density": "normal",
    "type": "sans",
    "size": "normal",
    "tone": "plain",
    "button": "rounded",
    "cardhead": "icon",
    "motion": "soft",
}
# 只影响写文案、不改样式的项（不写进 <html data-k-*>）
DESIGN_TEXT_ONLY = ("layout", "tone")
_HEX = re.compile(r"#[0-9a-fA-F]{6}")
NOTE_MAX = 200
# 拖进来的素材在成品里当什么：只当参考（给助手看、不放进成品）、Logo、主视觉图、背景、图库
ASSET_ROLES = {
    "ref": "只当参考",
    "logo": "Logo",
    "hero": "主视觉图",
    "bg": "背景",
    "gallery": "图库",
}
_SINGLE_ROLES = ("logo", "hero", "bg")
MATERIAL_EXT = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".gif": "image",
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
}


def look_of(raw: dict[str, Any], files: list[str]) -> dict[str, Any]:
    """看素材的结果：每张图看出来的版型、配色、材质、风格和借鉴点，加一句总结和合起来的色板。
    只认这次给它看的文件；颜色只认 #rrggbb。"""
    items = []
    seen: set[str] = set()
    for x in raw.get("items") or []:
        if not isinstance(x, dict):
            continue
        f = Path(str(x.get("file") or "")).name
        if f not in files or f in seen:
            continue
        seen.add(f)
        items.append(
            {
                "file": f,
                "what": _text(x.get("what"), 120),
                "layout": _text(x.get("layout"), 80),
                "palette": [c for c in map(hex_color, x.get("palette") or []) if c][:6],
                "material": _text(x.get("material"), 40),
                "style": [t for t in (_text(v, 12) for v in x.get("style") or []) if t][:5],
                "type": _text(x.get("type"), 40),
                "borrow": _text(x.get("borrow"), 120),
            }
        )
    palette = [c for c in map(hex_color, raw.get("palette") or []) if c]
    palette += [c for x in items for c in x["palette"]]
    return {
        "items": items,
        "summary": _text(raw.get("summary"), 300),
        "palette": list(dict.fromkeys(palette))[:10],
    }


def hex_color(v: Any) -> str:
    """#rrggbb（小写），不是就空。"""
    text = str(v or "").strip()
    return text.lower() if _HEX.fullmatch(text) else ""


def _fg_for(color: str) -> str:
    """主色上的字用黑还是白：按亮度挑对比更大的。"""
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (1, 3, 5))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    lum = 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
    return "#111418" if lum > 0.36 else "#ffffff"


# 选型台上每个窗口能调的宽度（12 列里占几列）
SPAN_CHOICES = (3, 4, 6, 8, 12)
REFS_MAX = 6


def design_catalog(themes: list[str] | None = None) -> list[dict[str, Any]]:
    """选型台左边的选项：每项的名字、分组和可选的值（配色从零件库的主题里来，主色是取色器）。"""
    themes = themes or manifest()["themes"]
    out = []
    for key, (label, group, opts) in DESIGN_DIMS.items():
        out.append(
            {
                "key": key,
                "label": label,
                "group": group,
                "options": [{"id": i, "label": t, "hint": h} for i, t, h in opts],
            }
        )
        if key == "herosize":
            out.append(
                {
                    "key": "theme",
                    "label": "配色",
                    "group": "色彩",
                    "options": [
                        {
                            "id": t,
                            "label": THEME_LABELS.get(t, (t, ""))[0],
                            "hint": THEME_LABELS.get(t, (t, ""))[1],
                        }
                        for t in themes
                    ],
                }
            )
            out.append(
                {
                    "key": "accent",
                    "label": "主色",
                    "group": "色彩",
                    "kind": "color",
                    "options": [],
                }
            )
    order = {g: n for n, g in enumerate(DESIGN_GROUPS)}
    return sorted(out, key=lambda d: order[d["group"]])


def design_brief(themes: list[str] | None = None) -> str:
    """给拆需求的助手看的选型目录。"""
    return "\n".join(
        f"- {d['key']}（{d['label']}）："
        + (
            "写 #rrggbb；配色里的主色就够用时留空"
            if d.get("kind") == "color"
            else "；".join(f"{o['id']} {o['label']}（{o['hint']}）" for o in d["options"])
        )
        for d in design_catalog(themes)
    )


def design_of(raw: Any, theme: str, themes: list[str]) -> dict[str, Any]:
    """拆需求的助手给的选型推荐：每项只认目录里的值，看不懂就用默认；参考作品最多 6 条。"""
    raw = raw if isinstance(raw, dict) else {}
    got = raw.get("picks") if isinstance(raw.get("picks"), dict) else {}
    why_raw = raw.get("why") if isinstance(raw.get("why"), dict) else {}
    picks: dict[str, str] = {}
    why: dict[str, str] = {}
    for d in design_catalog(themes):
        text = _text(why_raw.get(d["key"]), 120)
        if d.get("kind") == "color":
            picks[d["key"]] = hex_color(got.get(d["key"]))
            if text and picks[d["key"]]:
                why[d["key"]] = text
            continue
        ids = [o["id"] for o in d["options"]]
        v = str(got.get(d["key"]) or "")
        if d["key"] == "theme":
            v = theme
        if d["key"] == "layout":
            v = layout_id(v)
        picks[d["key"]] = v if v in ids else DESIGN_DEFAULTS.get(d["key"], ids[0])
        if text:
            why[d["key"]] = text
    refs = []
    for x in raw.get("refs") or []:
        if not isinstance(x, dict):
            continue
        name = _text(x.get("name"), 40)
        if name and name not in {r["name"] for r in refs}:
            refs.append({"name": name, "borrow": _text(x.get("borrow"), 120), "from": "方案"})
    return {
        "picks": picks,
        "recommend": dict(picks),
        "why": why,
        "refs": refs[:REFS_MAX],
        "tagline": _text(raw.get("tagline"), 30),
        "note": "",
        "assets": [],
    }


def _rows(ids: list[str], per: int) -> list[tuple[str, int]]:
    """按每行最多 per 个排，一行几个就平分 12 列（1 个占满、2 个各 6、3 个各 4、4 个各 3）。"""
    out = []
    for i in range(0, len(ids), per):
        row = ids[i : i + per]
        out += [(w, 12 // len(row)) for w in row]
    return out


# --- D 布局模板（2026-11-04）：6 种固定布局，电脑 / 平板 / 手机三种屏宽都按模板排 ---
# 每个窗口在 12 列的网格里有自己的格子（grid-template-areas），主视觉叫 hero，其余按顺序 a1、a2……
# 电脑按模板；平板主视觉占一整行、其余两个一行；手机一列、主视觉在最上面（外壳里主视觉排第一个）。
# 选型台的预览（web/js/benchgen.js 的 layoutGrid）和这里算法一样，端到端测试核对。
LAYOUT_TEMPLATES = ("hero-side", "hero-strip", "quad", "flow", "workspace", "stream")
LAYOUT_ALIAS = {"hero-wide": "hero-strip", "dashboard": "quad"}  # 旧规格里的版型名
# 拆需求的助手没挑版型时，按工作台类型给一个（类型名来自 bench.skills.kinds）
KIND_LAYOUT = {
    "editor": "workspace",
    "motion3d": "workspace",
    "content": "workspace",
    "audio": "workspace",
    "dashboard": "quad",
    "sim": "quad",
}
# 每个模板里格子的名字（主视觉以外按顺序），超出的排在下面
LAYOUT_ZONES = {
    "hero-side": ("侧栏列表（电脑上在主视觉右边上半）", "详情（电脑上在主视觉右边下半）"),
    "hero-strip": tuple(f"底部数据条第 {i} 格（主视觉下面一排）" for i in range(1, 5)),
    "quad": ("右上", "左下", "右下"),
    "flow": ("第 1 步（左边）", "第 3 步（右边）", "第 4 步（下面一整条，交付）"),
    "workspace": ("左侧工具栏（窄）", "右侧属性面板"),
    "stream": (),
}
HERO_ZONE = {
    "hero-side": "主视觉（左边占三分之二）",
    "hero-strip": "主视觉（占满一整行）",
    "quad": "主视觉（左上）",
    "flow": "第 2 步·主画面（中间）",
    "workspace": "中间画布（最宽）",
    "stream": "主视觉（最上面）",
}


def layout_id(v: Any) -> str:
    """认得的版型 id（旧名换成新名）；不认得就空。"""
    v = LAYOUT_ALIAS.get(str(v or ""), str(v or ""))
    return v if v in LAYOUT_TEMPLATES else ""


def default_layout(kind: str) -> str:
    return KIND_LAYOUT.get(kind, "hero-side")


def _row(ws: list[str], widths: list[int] | None = None) -> list[tuple[str, int]]:
    widths = widths or [12 // len(ws)] * len(ws)
    return list(zip(ws, widths, strict=True))


def _chunk(ws: list[str], per: int) -> list[list[tuple[str, int]]]:
    return [_row(ws[i : i + per]) for i in range(0, len(ws), per)]


def layout_grid(ids: list[str], hero: str, layout: str) -> dict[str, Any] | None:
    """某个模板下每个窗口的格子：电脑和平板的 grid-template-areas、每个窗口的格子名、宽度（列）、
    在版型里的位置、流程模板的步骤号。不是模板（旧规格的「按方案」）返回 None。"""
    layout = layout_id(layout)
    if not layout or not ids:
        return None
    h = hero if hero in ids else ids[0]
    rest = [w for w in ids if w != h]
    n = len(rest)
    rows: list[list[tuple[str, int]]]
    if layout == "hero-side":
        if n == 0:
            rows, used = [_row([h])], 0
        elif n == 1:
            rows, used = [_row([h, rest[0]], [8, 4])] * 2, 1
        else:
            rows, used = [_row([h, rest[0]], [8, 4]), _row([h, rest[1]], [8, 4])], 2
        rows += _chunk(rest[used:], 3)
    elif layout == "hero-strip":
        rows = [_row([h])] + _chunk(rest, 4)
    elif layout == "quad":
        rows = _chunk([h, *rest], 2)
    elif layout == "flow":
        if n == 0:
            rows, used = [_row([h])], 0
        elif n == 1:
            rows, used = [_row([rest[0], h], [4, 8])], 1
        else:
            rows, used = [_row([rest[0], h, rest[1]], [3, 6, 3])], 2
            if n >= 3:
                rows.append(_row([rest[2]]))
                used = 3
        rows += _chunk(rest[used:], 3)
    elif layout == "workspace":
        if n == 0:
            rows, used = [_row([h])], 0
        elif n == 1:
            rows, used = [_row([h, rest[0]], [9, 3])], 1
        else:
            rows, used = [_row([rest[0], h, rest[1]], [2, 7, 3])], 2
        rows += _chunk(rest[used:], 2)
    else:  # stream
        rows = [_row([w]) for w in [h, *rest]]
    area = {h: "hero"} | {w: f"a{i}" for i, w in enumerate(rest, 1)}
    zones = LAYOUT_ZONES[layout]
    zone = {h: HERO_ZONE[layout]} | {
        w: zones[i] if i < len(zones) else "下面（按顺序排）" for i, w in enumerate(rest)
    }
    if layout == "stream":
        zone |= {w: f"往下第 {i} 个" for i, w in enumerate(rest, 2)}
    if layout == "workspace" and n == 1:
        zone[rest[0]] = zones[1]  # 只有一个配角：放在画布右边当属性面板
    spans: dict[str, int] = {}
    for row in rows:
        for w, c in row:
            spans.setdefault(w, c)
    tab = [_row([h])] + _chunk(rest, 2)
    step = {}
    if layout == "flow":
        step = {w: k for k, w in enumerate([*rest[:1], h, *rest[1:3]], 1)}

    def areas(rs: list[list[tuple[str, int]]]) -> list[str]:
        return [" ".join(area[w] for w, c in r for _ in range(c)) for r in rs]

    return {
        "layout": layout,
        "order": [h, *rest],
        "desk": areas(rows),
        "tab": areas(tab),
        "area": area,
        "zone": zone,
        "spans": spans,
        "step": step,
    }


def layout_css(grid: dict[str, Any]) -> str:
    """外壳 <main> 的样式变量：电脑和平板各一套 grid-template-areas（手机一列，不用）。"""

    def q(rows: list[str]) -> str:
        return " ".join(f'"{r}"' for r in rows)

    return f"--k-areas: {q(grid['desk'])}; --k-areas-tab: {q(grid['tab'])}"


def layout_spans(spec: Spec, layout: str) -> list[tuple[str, int]]:
    """某个版型下窗口的顺序（主视觉第一个）和电脑上的宽度（12 列里占几列）。
    旧规格的「按方案」照拆需求时排的。选型台预览和签字后写回规格用同一份。"""
    grid = layout_grid([w.id for w in spec.windows], spec.hero, layout)
    if grid is None:
        return [(w.id, w.span) for w in spec.windows]
    return [(w, grid["spans"][w]) for w in grid["order"]]


def set_zones(spec: Spec) -> None:
    """按现在的版型给每个窗口写上它在哪一格（写窗口的助手照着排）。"""
    layout = ((spec.design or {}).get("picks") or {}).get("layout", "")
    grid = layout_grid([w.id for w in spec.windows], spec.hero, layout)
    for w in spec.windows:
        w.zone = grid["zone"][w.id] if grid else ""


def apply_design(
    spec: Spec, choice: dict[str, Any], materials: list[dict[str, Any]] | None = None
) -> list[str]:
    """签字时把你在选型台上点的写回规格：配色、样式、主色、风格要求、参考作品、素材的用处、
    去掉的窗口、版型和每个窗口的宽度。返回去掉的窗口 id。
    主视觉不能去掉，至少留两个窗口（只有一个就留着）。"""
    design = spec.design or design_of({}, spec.theme, manifest()["themes"])
    picks = dict(design.get("picks") or {})
    for d in design_catalog():
        if d.get("kind") == "color":
            if d["key"] in choice:
                picks[d["key"]] = hex_color(choice.get(d["key"]))
            continue
        v = str(choice.get(d["key"]) or "")
        if d["key"] == "layout":
            v = layout_id(v)
        if v in {o["id"] for o in d["options"]}:
            picks[d["key"]] = v
    if "note" in choice:
        design["note"] = _text(choice.get("note"), NOTE_MAX)
    roles = choice.get("assets") if isinstance(choice.get("assets"), dict) else {}
    if materials is not None:
        design["assets"] = assets_of(materials, roles)
    spec.theme = picks.get("theme", spec.theme)
    ids = [w.id for w in spec.windows]
    drop = [
        w
        for w in dict.fromkeys(str(x) for x in choice.get("drop") or [])
        if w in ids and w != spec.hero
    ][: max(0, len(ids) - 2)]
    if drop:
        spec.windows = [w for w in spec.windows if w.id not in drop]
        for e in spec.events:
            e["to"] = [x for x in e["to"] if x not in drop]
            if e["from"] in drop:
                e["from"] = ""
        for r in spec.requirements:
            r["windows"] = [x for x in r.get("windows") or [] if x not in drop]
        for st in spec.stages:
            st["windows"] = [x for x in st["windows"] if x not in drop]
        # 某段的窗口全去掉了：那段并进下一段（最后一段并进前一段），段号重排
        keep = [st for st in spec.stages if st["windows"]]
        for st in spec.stages:
            if not st["windows"] and keep:
                into = next((k for k in keep if k["n"] > st["n"]), keep[-1])
                into["accept"] = list(dict.fromkeys(st["accept"] + into["accept"]))
        for n, st in enumerate(keep, 1):
            st["n"] = n
        spec.stages = keep
    order = layout_spans(spec, picks.get("layout", ""))
    # 固定模板按格子排，宽窄跟模板走；只有旧规格的「按方案」还能单独调宽度
    templated = bool(layout_id(picks.get("layout", "")))
    spans = choice.get("spans") if isinstance(choice.get("spans"), dict) and not templated else {}
    by_id = {w.id: w for w in spec.windows}
    windows = []
    for wid, n in order:
        w = by_id[wid]
        try:
            want = int(spans.get(wid) or 0)
        except (TypeError, ValueError):
            want = 0
        w.span = want if want in SPAN_CHOICES else n
        windows.append(w)
    spec.windows = windows
    if "refs" in choice:
        keep = {str(x) for x in choice.get("refs") or []}
        design["refs"] = [r for r in design.get("refs") or [] if r["name"] in keep]
    spec.design = design | {"picks": picks, "chosen": True}
    set_zones(spec)
    return drop


def assets_of(materials: list[dict[str, Any]], roles: dict[str, Any]) -> list[dict[str, Any]]:
    """素材里标了用处（不是「只当参考」）的放进成品的 assets/。
    Logo、主视觉图、背景各只留最后一个；视频不能当 Logo 和背景。"""
    out: list[dict[str, Any]] = []
    for m in materials:
        role = str(roles.get(m["id"]) or "ref")
        if role not in ASSET_ROLES or role == "ref" or m.get("of"):
            continue
        if m["kind"] == "video" and role in ("logo", "bg"):
            continue
        if role in _SINGLE_ROLES:
            out = [a for a in out if a["role"] != role]
        out.append(
            {
                "id": m["id"],
                "name": m["name"],
                "kind": m["kind"],
                "role": role,
                "file": f"assets/{m['file']}",
            }
        )
    return out


def design_lines(spec: Spec) -> list[str]:
    """方案书和给写窗口的助手看的设计选型（大白话）。"""
    picks = (spec.design or {}).get("picks") or {}
    if not picks:
        return []
    names = {d["key"]: (d["label"], {o["id"]: o for o in d["options"]}) for d in design_catalog()}
    out = []
    for key, v in picks.items():
        if key == "accent" and v:
            out.append(f"主色：{v}（盖过配色里的主色）")
        elif key in names and v in names[key][1]:
            o = names[key][1][v]
            out.append(f"{names[key][0]}：{o['label']}（{o['hint']}）")
    d = spec.design or {}
    if d.get("note"):
        out.append(f"你的风格要求：{d['note']}")
    out += [
        f"素材「{a['name']}」当{ASSET_ROLES[a['role']]}，文件 {a['file']}"
        for a in d.get("assets") or []
    ]
    out += [
        f"参考：{r['name']}" + (f"，借鉴{r['borrow']}" if r.get("borrow") else "")
        for r in (spec.design or {}).get("refs") or []
    ]
    return out


def _requirements(
    raw: Any, seen: set[str], baseline: list[str], max_requirements: int
) -> list[dict[str, Any]]:
    """用户的要求编号 r1…，底线编号 b1…；底线以配置为准（拆需求的助手只能补「怎么检查、归谁」）。"""
    items = [x for x in (raw if isinstance(raw, list) else []) if isinstance(x, dict)]
    own = [x for x in items if not str(x.get("id") or "").startswith("b")]
    out: list[dict[str, Any]] = []

    def wins(x: dict[str, Any]) -> list[str]:
        got = [_ID.sub("-", str(w).lower()).strip("-") for w in (x.get("windows") or [])]
        return [w for w in got if w in seen][:8]

    for x in own:
        text = _text(x.get("text"), 160)
        if text and len(out) < max_requirements:
            out.append(
                {
                    "id": f"r{len(out) + 1}",
                    "source": "你的要求",
                    "text": text,
                    "check": _text(x.get("check"), 200),
                    "windows": wins(x),
                    "probe": probe_of(x.get("probe")),
                }
            )
    by_id = {str(x.get("id")): x for x in items}
    for n, text in enumerate(baseline, 1):
        x = by_id.get(f"b{n}", {})
        out.append(
            {
                "id": f"b{n}",
                "source": "底线",
                "text": text,
                "check": _text(x.get("check"), 200),
                "windows": wins(x),
                "probe": probe_of(x.get("probe")),
            }
        )
    return out


# 应用型产出的底座（K2）：名字 → 零件库里的文件。加底座 = 在 web/kit/ 下放一个目录 + 在这里登记
BASES: dict[str, dict[str, str]] = {
    "editor": {
        "label": "编辑器底座",
        "js": "/kit/editor/editor.js",
        "css": "/kit/editor/editor.css",
        "selftest": "/kit/editor/selftest.js",
        "api": "editor/API.md",
    },
}


def base_api(base: str) -> str:
    """底座的接口说明（给写功能的助手看）。"""
    info = BASES.get(base)
    if not info:
        return ""
    return (KIT_DIR / info["api"]).read_text(encoding="utf-8")


def normalize(
    raw: dict[str, Any],
    *,
    max_windows: int,
    baseline: list[str] | None = None,
    max_requirements: int = 12,
    max_stages: int = MAX_STAGES,
    kinds: set[str] | None = None,
    apps: dict[str, str] | None = None,
) -> Spec:
    """校验并修正：id 规范且不重复、零件和图标只留认得的、span 限在 3～12、窗口数不超上限。"""
    m = manifest()
    part_names = {p["name"] for p in m["parts"]}
    icons = icon_names()
    themes = m["themes"]
    title = _text(raw.get("title"), 24)
    if not title:
        raise SpecError("规格里没有标题")
    items = raw.get("windows") or []
    if not isinstance(items, list) or not items:
        raise SpecError("规格里没有窗口")
    windows: list[Window] = []
    seen: set[str] = set()
    for n, w in enumerate(items[:max_windows], 1):
        if not isinstance(w, dict):
            continue
        wid = _ID.sub("-", str(w.get("id") or "").lower()).strip("-")[:32] or f"w{n}"
        while wid in seen:
            wid = f"{wid}-{n}"
        seen.add(wid)
        try:
            span = int(w.get("span") or 6)
        except (TypeError, ValueError):
            span = 6
        icon = str(w.get("icon") or "")
        windows.append(
            Window(
                id=wid,
                title=_text(w.get("title"), 20) or f"窗口 {n}",
                sub=_text(w.get("sub"), 40),
                icon=icon if icon in icons else "layout-grid",
                span=max(3, min(12, span)),
                purpose=_text(w.get("purpose"), 400),
                parts=[p for p in (w.get("parts") or []) if p in part_names][:8],
                data=_text(w.get("data"), 600),
                actions=[_text(a, 40) for a in (w.get("actions") or []) if _text(a, 40)][:10],
                wants=wants_of(w.get("wants")),
            )
        )
    if not windows:
        raise SpecError("规格里的窗口都看不懂")
    icon = str(raw.get("icon") or "")
    theme = str(raw.get("theme") or "")
    needs = [
        {"need": _text(x.get("need"), 80), "keywords": _text(x.get("keywords"), 80)}
        for x in (raw.get("needs") or [])
        if isinstance(x, dict) and x.get("need")
    ][:6]
    shared = raw.get("shared") if isinstance(raw.get("shared"), dict) else {}
    if len(json.dumps(shared, ensure_ascii=False)) > SHARED_MAX_CHARS:
        raise SpecError(f"共用信息太长（超过 {SHARED_MAX_CHARS} 字）")
    events = []
    for e in raw.get("events") or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        name = re.sub(r"[^a-z0-9:-]+", "-", str(e["name"]).lower()).strip("-")[:40]
        if not name:
            continue
        sender = _ID.sub("-", str(e.get("from") or "").lower()).strip("-")
        receivers = [_ID.sub("-", str(x).lower()).strip("-") for x in (e.get("to") or [])]
        detail = e.get("detail") if isinstance(e.get("detail"), dict) else {}
        events.append(
            {
                "name": name,
                "from": sender if sender in seen else "",
                "to": [x for x in receivers if x in seen][:8],
                "detail": {str(k)[:30]: _text(v, 80) for k, v in list(detail.items())[:12]},
                "when": _text(e.get("when"), 80),
            }
        )
    state = {}
    for k, v in (raw.get("state") if isinstance(raw.get("state"), dict) else {}).items():
        key = re.sub(r"[^a-zA-Z0-9_-]+", "", str(k))[:30]
        if key and len(state) < STATE_MAX:
            state[key] = _text(v, 200)
    hero = _ID.sub("-", str(raw.get("hero") or "").lower()).strip("-")
    if hero in seen:
        # 主视觉排最前、占大半屏（参考资料里地图、3D、图纸都是画面的主体）
        windows.sort(key=lambda w: w.id != hero)
        windows[0].span = max(windows[0].span, 8)
    else:
        hero = ""
    requirements = _requirements(
        raw.get("requirements"), seen, list(baseline or []), max_requirements
    )
    theme = theme if theme in themes else "ink"
    stages = stages_of(
        raw.get("stages"), [w.id for w in windows], requirements, hero=hero, max_stages=max_stages
    )
    pool = [_text(x, 60) for x in (raw.get("pool") or []) if _text(x, 60)][:8]
    kind = str(raw.get("kind") or "")
    kind = kind if kind in (kinds or set()) else ""
    base = (apps or {}).get(kind, "") if kind else ""
    base = base if base in BASES else ""
    if base:
        hero = ""  # 应用型：画布就是主体，没有「主视觉窗口」
    raw_design = raw.get("design") if isinstance(raw.get("design"), dict) else {}
    raw_picks = raw_design.get("picks") if isinstance(raw_design.get("picks"), dict) else {}
    design = design_of(raw_design, theme, themes)
    if not layout_id(raw_picks.get("layout")):
        # 助手没挑版型（或挑了不认得的）：按工作台类型给一个
        design["picks"]["layout"] = design["recommend"]["layout"] = default_layout(kind)
    spec = Spec(
        title=title,
        subtitle=_text(raw.get("subtitle"), 60),
        icon=icon if icon in icons else "layers",
        theme=theme,
        windows=windows,
        needs=needs,
        shared=shared,
        events=events[:12],
        state=state,
        requirements=requirements,
        hero=hero,
        design=design,
        stages=stages,
        pool=pool,
        kind=kind,
        output="app" if base else "page",
        base=base,
    )
    set_zones(spec)
    return spec


# 外壳和卡片的模板（shell.html、card.html）：只引零件库，按规格排卡片；每个窗口的模块单独加载，
# 坏了只影响自己那一格（并在控制台报错，质检会抓到）。占位写成 %%名字%%。
_HERE = Path(__file__).with_name


def _fill(template: str, **values: object) -> str:
    return re.sub(r"%%(\w+)%%", lambda m: str(values[m.group(1)]), template)


def _design_attrs(spec: Spec) -> tuple[str, dict[str, Any]]:
    """选型写到 <html> 上的属性（data-k-*、主色），以及标了用处的素材（logo、背景……）。"""
    e = html.escape
    design = spec.design or {}
    picks = design.get("picks") or {}
    roles = {a["role"]: a for a in design.get("assets") or []}
    shown = dict(picks)
    if shown.get("bg") == "image" and "bg" not in roles:
        shown["bg"] = "soft"  # 没有标成背景的图就退回柔光
    attrs = "".join(
        f' data-k-{k}="{e(str(shown[k]))}"'
        for k in DESIGN_DEFAULTS
        if k not in DESIGN_TEXT_ONLY and shown.get(k) and shown[k] != DESIGN_DEFAULTS[k]
    )
    style = []
    if hex_color(picks.get("accent")):
        c = hex_color(picks.get("accent"))
        style += [f"--k-accent: {c}", f"--k-accent-fg: {_fg_for(c)}"]
    if shown.get("bg") == "image":
        style.append(f"--k-bg-image: url('{roles['bg']['file']}')")
    if style:
        attrs += f' style="{e("; ".join(style))}"'
    return attrs, roles


def _js(value: Any) -> str:
    """嵌进 <script> 的 JSON：</ 写成 <\\/，标题里有 </script> 也不会提前结束脚本。"""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def app_html(spec: Spec, bench_id: str) -> str:
    """应用型产出的首页（K1）：整页是底座（编辑器），每个窗口是底座上的一个功能模块。"""
    e = html.escape
    info = BASES[spec.base]
    attrs, roles = _design_attrs(spec)
    features = [{"id": w.id, "title": w.title, "icon": w.icon} for w in spec.windows]
    logo = (
        f'<span class="k-logo"><img src="{e(roles["logo"]["file"])}" alt="标志"></span>'
        if "logo" in roles
        else ""
    )
    return _fill(
        _HERE("app_shell.html").read_text(encoding="utf-8"),
        design=attrs,
        theme=e(spec.theme),
        title=e(spec.title),
        base_css=e(info["css"]),
        base_js=_js(info["js"]),
        selftest_js=_js(info["selftest"]),
        bench_id=_js(bench_id),
        options=_js({"title": spec.title, "icon": spec.icon, "logo": logo, "fileName": spec.title}),
        features=_js(features),
    )


def app_manifest(
    spec: Spec, *, checks: dict[str, Any] | None = None, samples: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """应用的清单 app.json：底座、功能、示例图、企业级验收的门槛、怎么自检。"""
    photo = next((s["file"] for s in samples or [] if s.get("kind") == "photo"), "")
    return {
        "version": 1,
        "title": spec.title,
        "kind": spec.kind,
        "base": spec.base,
        "features": [
            {"id": w.id, "title": w.title, "file": w.file, "data": w.data_file}
            for w in spec.windows
        ],
        "sample": photo,
        "checks": checks or {},
        "selftest": "index.html?selftest=1",
        "report": "report/",
    }


def write_app_manifest(
    folder: Path,
    spec: Spec,
    *,
    checks: dict[str, Any] | None = None,
    samples: list[dict[str, Any]] | None = None,
) -> None:
    old: dict[str, Any] = {}
    p = folder / "app.json"
    if p.is_file():
        try:
            old = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            old = {}
    data = app_manifest(spec, checks=checks or old.get("checks"), samples=samples)
    if samples is None:
        data["sample"] = old.get("sample", "")
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def shell_html(spec: Spec, bench_id: str) -> str:
    if spec.output == "app" and spec.base in BASES:
        return app_html(spec, bench_id)
    e = html.escape
    card = _HERE("card.html").read_text(encoding="utf-8").rstrip("\n")
    layout = ((spec.design or {}).get("picks") or {}).get("layout", "")
    grid = layout_grid([w.id for w in spec.windows], spec.hero, layout)
    by_id = {w.id: w for w in spec.windows}
    windows = [by_id[x] for x in grid["order"]] if grid else spec.windows

    def slot(w: Window) -> str:
        if grid is None:
            return ""
        out = f' data-slot="{grid["area"][w.id]}" style="--k-area: {grid["area"][w.id]}"'
        if w.id in grid["step"]:
            out += f' data-step="{grid["step"][w.id]}"'
        return out

    cards = "\n".join(
        _fill(
            card,
            span=w.span,
            slot=slot(w),
            hero=" k-hero" if w.id == spec.hero else "",
            id=w.id,
            icon=e(w.icon),
            title=e(w.title),
            sub=f'<div class="k-sub">{e(w.sub)}</div>' if w.sub else "",
        )
        for w in windows
    )
    main = (
        f' data-k-layout="{grid["layout"]}" style="{e(layout_css(grid))}"'
        if grid is not None
        else ""
    )
    attrs, roles = _design_attrs(spec)
    logo = (
        f'<span class="k-logo k-logo-img"><img src="{e(roles["logo"]["file"])}" alt="标志"></span>'
        if "logo" in roles
        else f'<span class="k-logo" data-icon="{e(spec.icon)}"></span>'
    )
    return _fill(
        _HERE("shell.html").read_text(encoding="utf-8"),
        design=attrs,
        logo=logo,
        theme=e(spec.theme),
        title=e(spec.title),
        subtitle=e(spec.subtitle),
        cards=cards,
        main=main,
        bench_id=json.dumps(bench_id),
    )


def write_shell(
    folder: Path,
    spec: Spec,
    bench_id: str,
    net: list[dict[str, Any]] | None = None,
    *,
    app_checks: dict[str, Any] | None = None,
) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "windows").mkdir(exist_ok=True)
    (folder / "data").mkdir(exist_ok=True)
    (folder / "spec.json").write_text(
        json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "data" / "_shared.json").write_text(
        json.dumps(spec.shared, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 能联网取的公开数据（名字、用途、网址模板、来源）：窗口经 ctx.net 取，地图零件自动用底图
    (folder / "data" / "_net.json").write_text(
        json.dumps(net or [], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if spec.output == "app":
        # 应用型：自带清单（底座、功能、验收门槛）和测试说明，报告和截图在 report/ 下
        write_app_manifest(folder, spec, checks=app_checks)
        (folder / "tests").mkdir(exist_ok=True)
        (folder / "tests" / "README.md").write_text(
            _HERE("app_tests.md").read_text("utf-8"), "utf-8"
        )
    path = folder / "index.html"
    path.write_text(shell_html(spec, bench_id), encoding="utf-8")
    return path


def load_spec(folder: Path) -> Spec:
    """读回拆需求时存下的规格（项目室在关口之间停下、之后接着做时用）。"""
    d = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    d["windows"] = [Window(**w) for w in d.get("windows") or []]
    return Spec(**d)
