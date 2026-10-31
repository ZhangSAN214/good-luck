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

from roundtable.core.benchkit.quality import KIT_DIR

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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def window(self, wid: str) -> Window | None:
        return next((w for w in self.windows if w.id == wid), None)


class SpecError(ValueError):
    pass


def _text(v: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


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
DESIGN_DIMS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    "layout": (
        "版型",
        [
            ("plan", "按方案", "照拆需求时排的宽窄和顺序"),
            ("hero-side", "主视觉 + 侧栏", "大画面在左占三分之二，列表和详情在右"),
            ("hero-wide", "主视觉通栏", "大画面占满一整行，其余窗口排在下面"),
            ("dashboard", "仪表盘", "窗口差不多大，一行三个，一眼看全"),
            ("workspace", "编辑器三栏", "左边工具、中间画布、右边属性，像修图和设计软件"),
            ("stream", "单列阅读", "从上到下一列，像文档、报告、教程"),
        ],
    ),
    "material": (
        "材质",
        [
            ("solid", "实色卡片", "白底卡片加轻阴影，最稳"),
            ("glass", "液态玻璃", "半透明磨砂，背后的颜色透出来"),
            ("paper", "纸感", "没有阴影、细边框，像纸面"),
            ("flat", "扁平", "没有边框和阴影，用底色分块"),
            ("outline", "线框", "透明底、粗一点的边框，像图纸"),
        ],
    ),
    "density": (
        "密度",
        [
            ("cozy", "宽松", "留白多，适合触屏和展示"),
            ("normal", "标准", "一般情况"),
            ("compact", "紧凑", "一屏放更多，适合数据多的"),
        ],
    ),
    "corner": (
        "圆角",
        [
            ("sharp", "方正", "小圆角，干练"),
            ("normal", "适中", "一般情况"),
            ("round", "圆润", "大圆角，亲和"),
        ],
    ),
    "type": (
        "字体气质",
        [
            ("sans", "现代无衬线", "清楚，一般情况"),
            ("serif", "衬线编辑感", "标题用宋体 / 衬线字，像杂志"),
            ("mono", "等宽技术感", "标题用等宽字，像终端和图纸"),
        ],
    ),
}
DESIGN_DEFAULTS = {
    "layout": "plan",
    "material": "solid",
    "density": "normal",
    "corner": "normal",
    "type": "sans",
}
# 选型台上每个窗口能调的宽度（12 列里占几列）
SPAN_CHOICES = (3, 4, 6, 8, 12)
REFS_MAX = 6


def design_catalog(themes: list[str] | None = None) -> list[dict[str, Any]]:
    """选型台左边的选项：每项的名字和可选的值（配色从零件库的主题里来）。"""
    themes = themes or manifest()["themes"]
    out = []
    for key, (label, opts) in DESIGN_DIMS.items():
        out.append(
            {
                "key": key,
                "label": label,
                "options": [{"id": i, "label": t, "hint": h} for i, t, h in opts],
            }
        )
        if key == "layout":
            out.append(
                {
                    "key": "theme",
                    "label": "配色",
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
    return out


def design_brief(themes: list[str] | None = None) -> str:
    """给拆需求的助手看的选型目录。"""
    return "\n".join(
        f"- {d['key']}（{d['label']}）："
        + "；".join(f"{o['id']} {o['label']}（{o['hint']}）" for o in d["options"])
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
        ids = [o["id"] for o in d["options"]]
        v = str(got.get(d["key"]) or "")
        if d["key"] == "theme":
            v = theme
        picks[d["key"]] = v if v in ids else DESIGN_DEFAULTS.get(d["key"], ids[0])
        text = _text(why_raw.get(d["key"]), 120)
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
    }


def _rows(ids: list[str], per: int) -> list[tuple[str, int]]:
    """按每行最多 per 个排，一行几个就平分 12 列（1 个占满、2 个各 6、3 个各 4、4 个各 3）。"""
    out = []
    for i in range(0, len(ids), per):
        row = ids[i : i + per]
        out += [(w, 12 // len(row)) for w in row]
    return out


def layout_spans(spec: Spec, layout: str) -> list[tuple[str, int]]:
    """某个版型下窗口的顺序和宽度（12 列里占几列）。选型台预览和签字后写回规格用同一份。"""
    plan = [(w.id, w.span) for w in spec.windows]
    ids = [w.id for w in spec.windows]
    hero = spec.hero if spec.hero in ids else ids[0]
    rest = [w for w in ids if w != hero]
    if layout == "hero-side":
        if not rest:
            return [(hero, 12)]
        return [(hero, 8), (rest[0], 4), *_rows(rest[1:], 3)]
    if layout == "hero-wide":
        return [(hero, 12), *_rows(rest, 3)]
    if layout == "dashboard":
        return _rows([hero, *rest], 3)
    if layout == "workspace":
        if len(rest) < 2:
            return layout_spans(spec, "hero-side")
        return [(rest[0], 3), (hero, 6), (rest[1], 3), *_rows(rest[2:], 2)]
    if layout == "stream":
        return [(w, 12) for w in [hero, *rest]]
    return plan


def apply_design(spec: Spec, choice: dict[str, Any]) -> list[str]:
    """签字时把你在选型台上点的写回规格：配色、样式、参考作品、去掉的窗口、版型和每个窗口的宽度。
    返回去掉的窗口 id。主视觉不能去掉，至少留两个窗口（只有一个就留着）。"""
    design = spec.design or design_of({}, spec.theme, manifest()["themes"])
    picks = dict(design.get("picks") or {})
    for d in design_catalog():
        v = str(choice.get(d["key"]) or "")
        if v in {o["id"] for o in d["options"]}:
            picks[d["key"]] = v
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
    order = layout_spans(spec, picks.get("layout", "plan"))
    spans = choice.get("spans") if isinstance(choice.get("spans"), dict) else {}
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
    return drop


def design_lines(spec: Spec) -> list[str]:
    """方案书和给写窗口的助手看的设计选型（大白话）。"""
    picks = (spec.design or {}).get("picks") or {}
    if not picks:
        return []
    names = {d["key"]: (d["label"], {o["id"]: o for o in d["options"]}) for d in design_catalog()}
    out = []
    for key, v in picks.items():
        if key in names and v in names[key][1]:
            o = names[key][1][v]
            out.append(f"{names[key][0]}：{o['label']}（{o['hint']}）")
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


def normalize(
    raw: dict[str, Any],
    *,
    max_windows: int,
    baseline: list[str] | None = None,
    max_requirements: int = 12,
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
    return Spec(
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
        design=design_of(raw.get("design"), theme, themes),
    )


# 外壳和卡片的模板（shell.html、card.html）：只引零件库，按规格排卡片；每个窗口的模块单独加载，
# 坏了只影响自己那一格（并在控制台报错，质检会抓到）。占位写成 %%名字%%。
_HERE = Path(__file__).with_name


def _fill(template: str, **values: object) -> str:
    return re.sub(r"%%(\w+)%%", lambda m: str(values[m.group(1)]), template)


def shell_html(spec: Spec, bench_id: str) -> str:
    e = html.escape
    card = _HERE("card.html").read_text(encoding="utf-8").rstrip("\n")
    cards = "\n".join(
        _fill(
            card,
            span=w.span,
            hero=" k-hero" if w.id == spec.hero else "",
            id=w.id,
            icon=e(w.icon),
            title=e(w.title),
            sub=f'<div class="k-sub">{e(w.sub)}</div>' if w.sub else "",
        )
        for w in spec.windows
    )
    picks = (spec.design or {}).get("picks") or {}
    attrs = "".join(
        f' data-k-{k}="{e(str(picks[k]))}"'
        for k in ("material", "density", "corner", "type")
        if picks.get(k) and picks[k] != DESIGN_DEFAULTS[k]
    )
    return _fill(
        _HERE("shell.html").read_text(encoding="utf-8"),
        design=attrs,
        theme=e(spec.theme),
        title=e(spec.title),
        subtitle=e(spec.subtitle),
        icon=e(spec.icon),
        cards=cards,
        bench_id=json.dumps(bench_id),
    )


def write_shell(
    folder: Path, spec: Spec, bench_id: str, net: list[dict[str, Any]] | None = None
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
    path = folder / "index.html"
    path.write_text(shell_html(spec, bench_id), encoding="utf-8")
    return path


def load_spec(folder: Path) -> Spec:
    """读回拆需求时存下的规格（项目室在关口之间停下、之后接着做时用）。"""
    d = json.loads((folder / "spec.json").read_text(encoding="utf-8"))
    d["windows"] = [Window(**w) for w in d.get("windows") or []]
    return Spec(**d)
